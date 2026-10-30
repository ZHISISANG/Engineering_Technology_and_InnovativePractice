"""流水线：一次运行 = 装载 → 校验 → 版本判定 → 草稿 → 分派 → 指标 → 验收 → 报告。

设计成“可从 run 目录重跑”的形式：输入副本、配置副本、确认记录都在 run 目录里，
任何人拿到目录都能复现同一结果（审计要求，f45）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from expo_trace.core import acceptance as acceptance_mod
from expo_trace.core import metrics as metrics_mod
from expo_trace.core import report as report_mod
from expo_trace.core.audit import AuditLog
from expo_trace.core.config import Config, load_config
from expo_trace.core.drafting import build_drafts
from expo_trace.core.ingest import EXPORT_FILES, MANIFEST_FILE, ingest
from expo_trace.core.models import (
    Disposition,
    Draft,
    IngestResult,
    Issue,
    QueueItem,
    Resolution,
    Ticket,
)
from expo_trace.core.routing import route
from expo_trace.core.store import RunStore
from expo_trace.core.validation import run_checks
from expo_trace.core.versioning import resolve_all
from expo_trace.utils.errors import InputError
from expo_trace.utils.textutil import sha256_bytes
from expo_trace.utils.timeutil import iso

OUTPUT_FILES = (
    "tickets.jsonl",
    "versions.jsonl",
    "resolutions.json",
    "issues.jsonl",
    "issues.csv",
    "drafts.jsonl",
    "queue.jsonl",
    "queue.csv",
    "checklist.md",
    "report.md",
    "metrics.json",
    "acceptance.json",
)


@dataclass
class RunResult:
    store: RunStore
    config: Config
    manifest: dict[str, Any]
    ingest: IngestResult
    tickets: list[Ticket]
    resolutions: dict[str, Resolution]
    issues: list[Issue]
    drafts: dict[str, Draft]
    dispositions: dict[str, Disposition]
    queue: list[QueueItem]
    metrics: dict[str, Any]
    acceptance: dict[str, Any]
    paused: bool
    report_path: Path | None = None
    manifest_path: Path | None = None


def _missing_export(export_dir: Path) -> list[str]:
    names = list(EXPORT_FILES) + [MANIFEST_FILE]
    if not export_dir.is_dir():
        return names
    return [name for name in names if not (export_dir / name).is_file()]


def _require_export(export_dir: Path) -> None:
    """先在源目录上把缺文件查出来，避免建了一半的运行目录留在磁盘上。"""
    missing = _missing_export(export_dir)
    if missing:
        raise InputError(f"导出目录缺少文件：{'、'.join(missing)}（目录：{export_dir}）")


def _copy_export(store: RunStore, export_dir: Path) -> dict[str, str]:
    _require_export(export_dir)
    names = list(EXPORT_FILES) + [MANIFEST_FILE]
    return store.copy_inputs({name: name for name in names}, export_dir)


def _write_outputs(
    result: RunResult,
    *,
    run_name: str,
    export_dir: Path,
    config_path: Path,
    actor: str,
    now: datetime,
    simulate_failure: bool,
) -> None:
    store = result.store
    store.write_json(
        "output/resolutions.json",
        {tid: res.__dict__ for tid, res in result.resolutions.items()},
    )
    store.write_jsonl("output/tickets.jsonl", [t.__dict__ for t in result.tickets])
    store.write_jsonl("output/versions.jsonl", [v.__dict__ for v in result.ingest.versions])
    store.write_jsonl("output/issues.jsonl", [i.__dict__ for i in result.issues])
    store.write_jsonl("output/drafts.jsonl", [d.__dict__ for d in result.drafts.values()])
    store.write_jsonl("output/queue.jsonl", [q.__dict__ for q in result.queue])
    store.write_text("output/issues.csv", report_mod.issues_csv(result.issues))
    store.write_text("output/queue.csv", report_mod.queue_csv(result.queue))
    store.write_text(
        "output/checklist.md",
        report_mod.render_checklist(result.tickets, result.resolutions, result.drafts, result.issues, result.config),
    )
    store.write_text(
        "output/report.md",
        report_mod.render_report(
            config=result.config,
            store=store,
            manifest=result.manifest,
            ingest=result.ingest,
            tickets=result.tickets,
            resolutions=result.resolutions,
            drafts=result.drafts,
            dispositions=result.dispositions,
            queue=result.queue,
            issues=result.issues,
            metrics=result.metrics,
            acceptance=result.acceptance,
            paused=result.paused,
        ),
    )
    store.write_json("output/metrics.json", result.metrics)
    store.write_json("output/acceptance.json", result.acceptance)
    for ticket_id, draft in result.drafts.items():
        store.write_text(f"output/drafts/{ticket_id}.md", draft.body + "\n")

    manifest = dict(result.manifest)
    manifest.update(
        {
            "run_name": run_name,
            "created_at": iso(now),
            "actor": actor,
            "export_dir": str(export_dir),
            "config_path": str(config_path),
            "degraded": result.ingest.degraded,
            "degraded_reason": result.ingest.degraded_reason,
            "snapshot_at": result.ingest.snapshot_at,
            "simulate_failure": simulate_failure,
            "counts": {
                "tickets": len(result.tickets),
                "versions": len(result.ingest.versions),
                "issues": len(result.issues),
                "queue": len(result.queue),
            },
            "dispositions": {k: v.value for k, v in result.dispositions.items()},
            "paused": result.paused,
            "pilot": {
                "batch": result.config.get("pilot.batch"),
                "weeks": result.config.get("pilot.weeks"),
                "staff": result.config.get("pilot.staff"),
                "weekly_hours_cap": result.config.get("pilot.weekly_hours_cap"),
            },
            "watermark": result.config.watermark,
            "audit_verify": result.metrics["audit"],
        }
    )
    store.write_json("manifest.json", manifest)
    result.manifest = manifest
    result.report_path = store.path("output/report.md")
    result.manifest_path = store.path("manifest.json")


def execute(
    *,
    export_dir: str | Path,
    run_dir: str | Path,
    config_path: str | Path = "config/default.yaml",
    actor: str | None = None,
    now: datetime | None = None,
    simulate_failure: bool = False,
    write: bool = True,
    run_name: str | None = None,
) -> RunResult:
    export_path = Path(export_dir).resolve()
    config_file = Path(config_path).resolve()
    config = load_config(config_file)
    if actor:
        config.data["actor"] = actor
    now = now or datetime.now().astimezone()
    run_path = Path(run_dir)
    if run_path.exists() and not run_path.is_dir():
        raise InputError(f"运行目录不可用：{run_path} 已存在且不是目录")
    # 先校验导出目录，再动运行目录：输入有误时不该留下空壳目录
    _require_export(export_path)
    store = RunStore(run_path)
    store.ensure()
    paused = store.is_paused()

    # 输入与配置副本：复现和审计都以 run 目录为准
    inputs = _copy_export(store, export_path)
    config_text = config_file.read_text(encoding="utf-8")
    store.write_text("config.yaml", config_text)

    # 成功读取一次就更新快照；模拟失败时使用上一份快照
    export_meta: dict[str, Any] = {}
    if not simulate_failure:
        try:
            import json

            manifest_text = (export_path / MANIFEST_FILE).read_text(encoding="utf-8-sig")
            export_meta = json.loads(manifest_text)
        except (OSError, ValueError):
            export_meta = {}
        store.save_snapshot(
            {name: name for name in EXPORT_FILES},
            export_path,
            {"saved_at": iso(now), "export_meta": export_meta},
        )

    result_ingest = ingest(store, config, now, simulate_failure=simulate_failure)
    if not result_ingest.export_meta and export_meta:
        result_ingest.export_meta = export_meta
    AuditLog(store, actor=config.actor).append(
        "run_started",
        run_path.name,
        {
            "export_dir": str(export_path),
            "config": str(config_file),
            "simulate_failure": simulate_failure,
            "degraded": result_ingest.degraded,
        },
        at=now,
    )
    tickets = result_ingest.tickets
    unconfirmed = tuple(str(x) for x in config.get("quality.unconfirmed_tokens", []))
    resolutions = resolve_all(tickets, result_ingest.versions, unconfirmed)
    issues = run_checks(result_ingest, config, resolutions, now)

    # 每张工单的版本判定单独入审计：事后能回答“当时用的是哪一版、为什么”。
    audit = AuditLog(store, actor=config.actor)
    for ticket in tickets:
        resolution = resolutions[ticket.ticket_id]
        audit.append(
            "version_resolved",
            ticket.ticket_id,
            {
                "ticket_id": ticket.ticket_id,
                "batch": ticket.batch,
                "state": str(getattr(resolution.state, "value", resolution.state)),
                "version_id": resolution.version_id or "",
                "reason_code": resolution.reason_code or "",
            },
            at=now,
        )
    reviews = store.reviews()
    drafts = build_drafts(tickets, resolutions, result_ingest.versions, issues, config, reviews, paused, now)
    dispositions, queue = route(
        tickets, resolutions, drafts, issues, config, reviews, paused, now
    )

    manifest = {
        "inputs": inputs,
        "config_sha256": sha256_bytes(config_text.encode("utf-8")),
        "export_meta": result_ingest.export_meta,
    }
    result = RunResult(
        store=store,
        config=config,
        manifest=manifest,
        ingest=result_ingest,
        tickets=tickets,
        resolutions=resolutions,
        issues=issues,
        drafts=drafts,
        dispositions=dispositions,
        queue=queue,
        metrics={},
        acceptance={},
        paused=paused,
    )
    result.metrics = metrics_mod.compute(
        tickets, resolutions, drafts, dispositions, queue, issues, reviews, config, store, paused, now
    )
    publish_blocked = _publish_blocked()
    result.acceptance = acceptance_mod.evaluate(
        config,
        result_ingest,
        tickets,
        resolutions,
        drafts,
        queue,
        dispositions,
        issues,
        result.metrics,
        publish_blocked,
    )

    if write:
        _write_outputs(
            result,
            run_name=run_name or run_path.name,
            export_dir=export_path,
            config_path=config_file,
            actor=config.actor,
            now=now,
            simulate_failure=simulate_failure,
        )
        audit = AuditLog(store, actor=config.actor)
        audit.append(
            "run_completed",
            run_path.name,
            {
                "inputs": inputs,
                "degraded": result.ingest.degraded,
                "counts": {
                    "tickets": len(tickets),
                    "drafts": len(drafts),
                    "queue": len(queue),
                },
                "dispositions": {k: v.value for k, v in dispositions.items()},
                "paused": paused,
            },
            at=now,
        )
    return result


def _publish_blocked() -> bool:
    from expo_trace.core import drafting

    try:
        drafting.publish()
    except Exception:
        return True
    return False


def status(run_dir: str | Path) -> dict[str, Any]:
    store = RunStore(run_dir)
    if not store.path("manifest.json").is_file():
        raise InputError(f"运行目录没有 manifest.json：{store.root}")
    manifest = store.read_json("manifest.json")
    manifest["reviews"] = len(store.reviews())
    manifest["paused_now"] = store.is_paused()
    manifest["report_exists"] = store.path("output/report.md").is_file()
    return manifest


def clean_run(run_dir: str | Path) -> None:
    """删除输出但保留输入/确认/审计（审计链路不能因清理而断）。"""
    store = RunStore(run_dir)
    for rel in ("output",):
        target = store.path(rel)
        if target.exists():
            shutil.rmtree(target)
