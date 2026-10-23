"""输入装载：把三类工具（服务台/台账/文件库）的脱敏只读导出读成领域模型。

设计要点：
- 原始行始终保留在 ``Ticket.raw`` / ``Version.raw`` 里，问题记录不覆盖原值（f33）。
- 读取失败时切到上次快照，并显式标记时间与“需人工核查”，绝不编造最新状态（f35）。
- 只读是硬约束：导出清单声明可写、或要求写回时，直接拒绝启动（f32）。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from expo_trace.core.config import Config
from expo_trace.core.codes import Code
from expo_trace.core.models import (
    Handoff,
    IngestResult,
    Issue,
    KnowledgeVersion,
    LedgerRow,
    Ticket,
)
from expo_trace.core.store import RunStore
from expo_trace.utils.errors import InputError, SnapshotFallback
from expo_trace.utils.textutil import mask_pii
from expo_trace.utils.timeutil import gap_hours, parse_time, parse_duration_minutes

EXPORT_FILES = ("service_desk.csv", "ledger.csv", "file_index.csv", "handoff.csv")
MANIFEST_FILE = "manifest.json"


def _normalize_header(name: str) -> str:
    return str(name).strip().lstrip("\ufeff")


def _read_csv(text: str, source: str) -> list[tuple[int, dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise InputError(f"{source} 没有表头")
    rows: list[tuple[int, dict[str, str]]] = []
    for raw in reader:
        # 个人字段在进入运行目录之前就脱敏；raw 仍保留原行结构与业务字段（f32/f43）。
        row = {
            _normalize_header(k): mask_pii("" if v is None else str(v).strip())
            for k, v in raw.items()
            if k is not None
        }
        if not any(row.values()):
            continue
        rows.append((reader.line_num, row))
    return rows


def pick(row: dict[str, str], aliases: list[str], default: str = "") -> tuple[str, str]:
    """按别名表取值，返回 ``(值, 命中的列名)``；未命中时列名为空。"""
    lowered = {k.strip().lower(): k for k in row}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lowered:
            return row[lowered[key]], lowered[key]
    return default, ""


def _truthy(raw: str) -> bool:
    return str(raw).strip().lower() in {"是", "true", "1", "yes", "y", "已确认", "confirmed", "已作废", "作废"}


def _issue(
    code: str,
    ticket_id: str | None,
    field: str,
    detail: str,
    source: str = "",
    line: int = 0,
    raw: dict[str, Any] | None = None,
) -> Issue:
    from expo_trace.core.codes import severity

    return Issue(
        code=code,
        ticket_id=ticket_id,
        field_name=field,
        detail=detail,
        source=source,
        line=line,
        raw=raw or {},
        severity=severity(code),
    )


def _snapshot(store: RunStore) -> tuple[dict[str, str], dict[str, Any]]:
    try:
        return store.load_snapshot()
    except SnapshotFallback as exc:
        raise InputError(f"读取输入失败，且没有可用快照：{exc.reason}") from exc


def read_export(
    store: RunStore,
    config: Config,
    simulate_failure: bool = False,
) -> tuple[dict[str, str], dict[str, Any], bool, str, str]:
    """读取输入文件；返回 ``(文本, 清单, 是否降级, 降级原因, 快照时间)``。"""
    if simulate_failure:
        files, meta = _snapshot(store)
        return (
            files,
            dict(meta.get("export_meta", {})),
            True,
            "演练模拟：读取失败，已切换到上次快照并标记时间，需人工核查",
            str(meta.get("saved_at", "")),
        )
    try:
        texts = {
            name: store.path("input", name).read_text(encoding="utf-8-sig")
            for name in EXPORT_FILES
        }
        manifest_text = store.path("input", MANIFEST_FILE).read_text(encoding="utf-8-sig")
        manifest = json.loads(manifest_text)
    except (OSError, json.JSONDecodeError) as exc:
        files, meta = _snapshot(store)
        return (
            files,
            dict(meta.get("export_meta", {})),
            True,
            f"读取输入失败（{exc}），已切换到上次快照并标记时间，需人工核查",
            str(meta.get("saved_at", "")),
        )
    if manifest.get("read_only") is False or str(manifest.get("mode", "")).startswith("write"):
        raise InputError("导出清单声明了可写或写回模式，只读试点拒绝处理")
    return texts, manifest, False, "", ""


def ingest(
    store: RunStore,
    config: Config,
    now: datetime,
    simulate_failure: bool = False,
) -> IngestResult:
    texts, manifest, degraded, degraded_reason, snapshot_at = read_export(
        store, config, simulate_failure=simulate_failure
    )
    result = IngestResult(
        export_meta=manifest,
        degraded=degraded,
        degraded_reason=degraded_reason,
        snapshot_at=snapshot_at,
    )
    unconfirmed_tokens = tuple(str(x) for x in config.get("quality.unconfirmed_tokens", []))
    ticket_map = config.mapping("tickets")
    version_map = config.mapping("versions")
    ledger_map = config.mapping("ledger")
    handoff_map = config.mapping("handoff")

    # ---- 工单（服务台） ----
    seen: dict[str, dict[str, Any]] = {}
    for line, row in _read_csv(texts.get("service_desk.csv", ""), "service_desk.csv"):
        ticket_id, _ = pick(row, ticket_map.get("ticket_id", []))
        if not ticket_id:
            result.issues.append(
                _issue(Code.FIELD_UNMAPPED, None, "ticket_id", "工单行缺少可识别的工单编号，已跳过", "service_desk.csv", line, row)
            )
            continue
        if ticket_id in seen:
            result.issues.append(
                _issue(
                    Code.DUPLICATE_ID,
                    ticket_id,
                    "ticket_id",
                    f"同一工单编号在服务台导出中出现多次（首行 {seen[ticket_id]['_line']} 行，本行 {line} 行）",
                    "service_desk.csv",
                    line,
                    row,
                )
            )
            continue
        seen[ticket_id] = {"_line": line, "_raw": row}

        batch, _ = pick(row, ticket_map.get("batch", []))
        category, _ = pick(row, ticket_map.get("category", []))
        question, _ = pick(row, ticket_map.get("question", []))
        occurred_raw, _ = pick(row, ticket_map.get("occurred_at", []))
        received_raw, _ = pick(row, ticket_map.get("received_at", []))
        assignee, _ = pick(row, ticket_map.get("assignee", []))
        contact, _ = pick(row, ticket_map.get("contact", []))

        occurred = parse_time(occurred_raw, unconfirmed_tokens)
        received = parse_time(received_raw, unconfirmed_tokens)
        ticket = Ticket(
            ticket_id=ticket_id,
            batch=batch,
            category=category,
            question=mask_pii(question),
            occurred=occurred,
            received=received,
            assignee=assignee,
            contact=mask_pii(contact),
            source="service_desk.csv",
            line=line,
            raw=row,
        )
        result.tickets.append(ticket)

    # ---- 版本（文件库） ----
    for line, row in _read_csv(texts.get("file_index.csv", ""), "file_index.csv"):
        version_id, _ = pick(row, version_map.get("version_id", []))
        batch, _ = pick(row, version_map.get("batch", []))
        category, _ = pick(row, version_map.get("category", []))
        effective_raw, _ = pick(row, version_map.get("effective_from", []))
        effective_from = parse_time(effective_raw, unconfirmed_tokens)
        if not version_id or not effective_from.ok:
            result.issues.append(
                _issue(
                    Code.KEY_FIELD_MISSING,
                    None,
                    "version_id/effective_from",
                    f"版本记录缺少版本编号或生效起始时间（版本编号={version_id or '空'}），已跳过",
                    "file_index.csv",
                    line,
                    row,
                )
            )
            continue
        effective_to_raw, _ = pick(row, version_map.get("effective_to", []))
        effective_to = parse_time(effective_to_raw, unconfirmed_tokens)
        confirmed_raw, _ = pick(row, version_map.get("confirmed", []))
        confirmed_at_raw, _ = pick(row, version_map.get("confirmed_at", []))
        void_raw, _ = pick(row, version_map.get("void", []))
        received_raw, _ = pick(row, version_map.get("received_at", []), default="")
        result.versions.append(
            KnowledgeVersion(
                version_id=version_id,
                notice_id=pick(row, version_map.get("notice_id", []))[0],
                batch=batch,
                category=category,
                effective_from=effective_from.value,  # type: ignore[arg-type]
                effective_to=effective_to.value if effective_to.ok else None,
                confirmed=_truthy(confirmed_raw),
                confirmed_by=pick(row, version_map.get("confirmed_by", []))[0],
                confirmed_at=parse_time(confirmed_at_raw, unconfirmed_tokens).value,
                void=_truthy(void_raw),
                void_reason=pick(row, version_map.get("void_reason", []))[0],
                supersedes=pick(row, version_map.get("supersedes", []))[0],
                file_name=pick(row, version_map.get("file_name", []))[0],
                statement=pick(row, version_map.get("statement", []))[0],
                received_at=parse_time(received_raw, unconfirmed_tokens).value,
                source="file_index.csv",
                line=line,
                raw=row,
            )
        )

    # ---- 台账（工时/状态/截止） ----
    ledger_by_id: dict[str, LedgerRow] = {}
    for line, row in _read_csv(texts.get("ledger.csv", ""), "ledger.csv"):
        ticket_id, _ = pick(row, ledger_map.get("ticket_id", []))
        handling_raw, _ = pick(row, ledger_map.get("handling", []))
        unit_raw, _ = pick(row, ledger_map.get("unit", []))
        minutes, unit_note = parse_duration_minutes(handling_raw, unit_raw)
        ledger = LedgerRow(
            ticket_id=ticket_id,
            handling_minutes=minutes,
            unit_note=unit_note,
            deadline=pick(row, ledger_map.get("deadline", []))[0],
            status=pick(row, ledger_map.get("status", []))[0],
            source="ledger.csv",
            line=line,
            raw=row,
        )
        result.ledger.append(ledger)
        if ticket_id:
            ledger_by_id[ticket_id] = ledger

    # ---- 交接记录 ----
    handoff_by_id: dict[str, Handoff] = {}
    for line, row in _read_csv(texts.get("handoff.csv", ""), "handoff.csv"):
        ticket_id, _ = pick(row, handoff_map.get("ticket_id", []))
        if not ticket_id:
            continue
        items_raw, _ = pick(row, handoff_map.get("unconfirmed_items", []))
        handoff = Handoff(
            ticket_id=ticket_id,
            from_role=pick(row, handoff_map.get("from_role", []))[0],
            to_role=pick(row, handoff_map.get("to_role", []))[0],
            latest_version=pick(row, handoff_map.get("latest_version", []))[0],
            unconfirmed_items=[x.strip() for x in items_raw.replace("；", ";").replace("、", ";").split(";") if x.strip()],
            due_at=pick(row, handoff_map.get("due_at", []))[0],
            note=pick(row, handoff_map.get("note", []))[0],
            source="handoff.csv",
            line=line,
            raw=row,
        )
        handoff_by_id.setdefault(ticket_id, handoff)

    for ticket in result.tickets:
        ledger = ledger_by_id.get(ticket.ticket_id)
        if ledger:
            ticket.handling_minutes = ledger.handling_minutes
            ticket.handling_note = ledger.unit_note
            ticket.deadline = ledger.deadline
            ticket.status = ledger.status
        else:
            result.issues.append(
                _issue(
                    Code.SOURCE_INCOMPLETE,
                    ticket.ticket_id,
                    "ledger",
                    "业务台账中没有该工单的处理记录，工时与截止时间不可核",
                    "ledger.csv",
                    0,
                    {},
                )
            )
        ticket.handoff = handoff_by_id.get(ticket.ticket_id)
        gap = gap_hours(ticket.occurred_at, ticket.received_at)
        if gap is not None:
            ticket.backfill_gap = gap
            ticket.backfilled = gap > float(config.get("quality.backfill_hours", 8))

    # 台账中存在但服务台没有的工单，不静默丢弃
    known = {t.ticket_id for t in result.tickets}
    for ledger in result.ledger:
        if ledger.ticket_id and ledger.ticket_id not in known:
            result.issues.append(
                _issue(
                    Code.SOURCE_INCOMPLETE,
                    ledger.ticket_id,
                    "ledger",
                    "台账记录在服务台导出中找不到对应工单（来源不一致）",
                    "ledger.csv",
                    ledger.line,
                    ledger.raw,
                )
            )
    return result
