"""命令行入口：``expo-trace``。

命令设计遵循一条原则：**做不了的事不提供开关**。
没有 ``--publish``、没有 ``--write-back``、没有 ``--send``——因为这些能力不存在。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from expo_trace import __version__
from expo_trace.core.audit import AuditLog
from expo_trace.core.config import load_config
from expo_trace.core.feedback import submit_feedback
from expo_trace.core.metrics import sample_plan
from expo_trace.core.models import Disposition, Draft
from expo_trace.core.pipeline import execute, status
from expo_trace.core.review import submit_review
from expo_trace.core.store import RunStore
from expo_trace.utils.errors import (
    BlockedAction,
    ConfigError,
    InputError,
    PausedError,
    ReviewRejected,
    TraceError,
)
from expo_trace.utils.jsons import dumps, jsonable
from expo_trace.utils.textutil import ensure_utf8_stdio
from expo_trace.utils.timeutil import parse_time

DEFAULT_EXPORT = "data/demo/export"
DEFAULT_RUN = "runs/demo"


def _now(text: str | None) -> datetime:
    if not text:
        return datetime.now().astimezone()
    parsed = parse_time(text)
    if not parsed.ok:
        raise InputError(f"--now 不是可识别的时间：{text!r}")
    return parsed.value  # type: ignore[return-value]


def _print_summary(result: Any) -> None:
    metrics = result.metrics
    counts = metrics["dispositions"]
    print(f"运行目录：{result.store.root.as_posix()}")
    print(f"输入：{len(result.manifest.get('inputs', {}))} 个文件（sha256 已记录）")
    if result.ingest.degraded:
        print(f"降级：是 —— {result.ingest.degraded_reason}（快照 {result.ingest.snapshot_at}）")
    else:
        print("降级：否")
    print(
        "工单：{tickets}  可出草稿：{draft}  人工队列：{human}  阻塞：{blocked}".format(
            tickets=metrics["tickets"],
            draft=counts.get("draft_ready", 0),
            human=counts.get("human_review", 0),
            blocked=counts.get("blocked", 0),
        )
    )
    print(
        "问题：{total}（error {errors} / warn {warnings}）".format(**metrics["issues"])
    )
    print(
        "指标：工时 {h} 分钟 · 返工 {r} 次 · 确认完整率 {c:.1%}".format(
            h=metrics["handling"]["total_minutes"],
            r=metrics["rework"]["total"],
            c=metrics["confirmation"]["rate"],
        )
    )
    summary = result.acceptance["summary"]
    print(f"验收（工具侧）：{summary['tool_side']}；{summary['business']}")
    if result.report_path:
        print(f"产物：{result.report_path.as_posix()}")
    if result.paused:
        print("注意：试点处于暂停状态，草稿不得对外使用。")


def cmd_run(args: argparse.Namespace) -> int:
    result = execute(
        export_dir=args.export,
        run_dir=args.run_dir,
        config_path=args.config,
        actor=args.actor,
        now=_now(args.now),
        simulate_failure=args.simulate_failure,
        run_name=args.run_name,
    )
    if args.json:
        print(
            dumps(
                {
                    "run_dir": str(result.store.root),
                    "manifest": result.manifest,
                    "metrics": result.metrics,
                    "acceptance": result.acceptance,
                }
            )
        )
    else:
        _print_summary(result)
    if result.paused:
        return PausedError.exit_code
    if args.strict and result.acceptance["summary"]["fail"]:
        return 4
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = RunStore(args.run_dir)
    if not store.exists():
        raise InputError("运行目录不存在，请先执行 run")
    record = submit_feedback(
        store,
        config,
        ticket_id=args.ticket,
        kind=args.kind,
        reporter=args.reporter,
        note=args.note,
        now=_now(args.now),
    )
    if args.json:
        print(dumps(record))
    else:
        print(f"已登记：{record['ticket_id']} · {record['kind_label']} · {record['reporter']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    data = status(args.run_dir)
    if args.json:
        print(dumps(data))
        return 0
    print(f"运行：{data.get('run_name', '-')}  批次：{data.get('pilot', {}).get('batch', '-')}")
    print(f"时间：{data.get('created_at', '-')}  操作者：{data.get('actor', '-')}")
    if data.get("degraded"):
        print(f"降级：是 —— {data.get('degraded_reason')}（快照 {data.get('snapshot_at')}）")
    counts = data.get("counts", {})
    print(f"工单 {counts.get('tickets', 0)} · 版本 {counts.get('versions', 0)} · 问题 {counts.get('issues', 0)} · 队列 {counts.get('queue', 0)}")
    dispositions = data.get("dispositions", {}) or {}
    tally = {"draft_ready": 0, "human_review": 0, "blocked": 0}
    for value in dispositions.values():
        key = value.value if isinstance(value, Disposition) else str(value)
        tally[key] = tally.get(key, 0) + 1
    print(
        "分派：可出草稿 {draft_ready} / 人工 {human_review} / 阻塞 {blocked}".format(
            draft_ready=tally["draft_ready"],
            human_review=tally["human_review"],
            blocked=tally["blocked"],
        )
    )
    print(f"确认记录：{data.get('reviews', 0)}  当前暂停：{'是' if data.get('paused_now') else '否'}")
    if data.get("report_exists"):
        print(f"报告：{Path(args.run_dir, 'output', 'report.md').as_posix()}")
    return 0


def _load_draft(store: RunStore, ticket_id: str) -> Draft:
    rows = store.read_jsonl("output/drafts.jsonl")
    if not rows:
        raise InputError("运行目录里没有草稿产物，请先执行 run")
    for row in rows:
        if row.get("ticket_id") == ticket_id:
            row = dict(row)
            row["status"] = Disposition(row["status"])
            return Draft(**row)
    raise InputError(f"找不到工单 {ticket_id} 的草稿；请确认编号是否正确")


def cmd_review(args: argparse.Namespace) -> int:
    store = RunStore(args.run_dir)
    config = load_config(store.path("config.yaml") if store.path("config.yaml").is_file() else args.config)
    draft = _load_draft(store, args.ticket)
    payload = submit_review(
        store,
        config,
        ticket_id=args.ticket,
        reviewer=args.reviewer,
        decision=args.decision,
        note=args.note,
        confirmed_items=list(args.item or []),
        draft=draft,
        now=_now(args.now),
    )
    if args.json:
        print(dumps(payload))
    else:
        print(f"已记录确认：{payload['review_id']}（{payload['decision']}，{payload['reviewer']}）")
        print("提醒：确认的是草稿与来源，不等于对外授权；发布仍需人工完成。")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    store = RunStore(args.run_dir)
    config = load_config(store.path("config.yaml") if store.path("config.yaml").is_file() else args.config)
    if args.actor not in config.pause_roles():
        raise ReviewRejected(
            f"{args.actor} 无权暂停试点；只有 {'、'.join(config.pause_roles())} 可以操作（f04）"
        )
    store.pause(args.actor, args.reason, _now(args.now))
    AuditLog(store, actor=args.actor).append(
        "pilot_paused", store.root.name, {"reason": args.reason}, at=_now(args.now)
    )
    print(f"试点已暂停（操作人 {args.actor}）：{args.reason}")
    print("暂停期间不生成新草稿；已生成草稿不得对外使用。恢复请执行 resume。")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    store = RunStore(args.run_dir)
    config = load_config(store.path("config.yaml") if store.path("config.yaml").is_file() else args.config)
    if args.actor not in config.pause_roles():
        raise ReviewRejected(
            f"{args.actor} 无权恢复试点；只有 {'、'.join(config.pause_roles())} 可以操作"
        )
    store.resume()
    AuditLog(store, actor=args.actor).append("pilot_resumed", store.root.name, {}, at=_now(args.now))
    print(f"试点已恢复（操作人 {args.actor}）。")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store = RunStore(args.run_dir)
    if not store.path("config.yaml").is_file():
        raise InputError("运行目录缺少 config.yaml，无法重跑；请重新执行 run")
    result = execute(
        export_dir=store.path("input"),
        run_dir=args.run_dir,
        config_path=store.path("config.yaml"),
        actor=args.actor,
        now=_now(args.now),
        simulate_failure=False,
        write=True,
        run_name=store.root.name,
    )
    if args.json:
        print(dumps({"report": str(result.report_path), "metrics": result.metrics}))
    else:
        print(f"已重跑并更新：{result.report_path}")
        print("输入与配置均取自运行目录，结果可复现。")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    store = RunStore(args.run_dir)
    log = AuditLog(store, actor=args.actor or "审计查看")
    if args.action == "verify":
        result = log.verify()
        if args.json:
            print(dumps(result))
        elif result["ok"]:
            print(f"审计链完整：{result['entries']} 条记录，哈希链无断点。")
        else:
            print(f"审计链断裂：seq={result['broken_at']}，{result['reason']}")
        return 0 if result["ok"] else 2

    entries = log.read()
    if args.ticket:
        # 只按“对象”匹配，避免把包含该编号的汇总事件误当成工单事件。
        entries = [
            e
            for e in entries
            if e.get("subject") == args.ticket
            or e.get("details", {}).get("ticket_id") == args.ticket
        ]
    entries = entries[-args.limit :]
    if args.json:
        print(dumps(entries))
    else:
        print(f"{'seq':>4}  {'时间':<20}  {'操作人':<10}  {'动作':<18}  对象/摘要")
        for entry in entries:
            details = entry.get("details", {})
            if entry.get("action") == "version_resolved":
                summary = (
                    f"版本 {details.get('version_id') or '（无）'} · "
                    f"{details.get('state') or '-'} · {details.get('reason_code') or '-'}"
                )
            else:
                summary = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
            print(
                f"{entry.get('seq', 0):>4}  {entry.get('at', '')[:19]:<20}  "
                f"{str(entry.get('actor', ''))[:10]:<10}  {str(entry.get('action', ''))[:18]:<18}  "
                f"{entry.get('subject', '')} {summary[:110]}"
            )
    if args.ticket:
        log.append("audit_viewed", args.ticket, {"filter": args.ticket}, at=_now(args.now))
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    store = RunStore(args.run_dir)
    rows = store.read_jsonl("output/tickets.jsonl")
    proxies = [
        SimpleNamespace(ticket_id=row["ticket_id"], category=row.get("category") or "未分类")
        for row in rows
    ]
    plan = sample_plan(proxies, args.size)  # type: ignore[arg-type]
    if args.json:
        print(dumps({"size": args.size, "available": len(rows), "tickets": plan}))
    else:
        print(f"抽查计划：目标 {args.size} 条，可抽 {len(plan)} 条（共 {len(rows)} 条工单）")
        print("、".join(plan) if plan else "无可抽样本")
        print("提示：合成数据不能替代真实抽查；正式抽查量是否沿用 80 条需审批。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expo-trace",
        description="展商咨询工单 · 版本可追溯只读试点工具（只读、只出草稿、可审计）",
    )
    parser.add_argument("--version", action="version", version=f"expo-trace {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑完整流水线并生成报告")
    run.add_argument("--export", default=DEFAULT_EXPORT, help="脱敏只读导出目录")
    run.add_argument("--run-dir", default=DEFAULT_RUN, help="运行目录")
    run.add_argument("--config", default="config/default.yaml", help="配置文件")
    run.add_argument("--actor", default=None, help="操作者（默认取配置）")
    run.add_argument("--run-name", default=None)
    run.add_argument("--now", default=None, help="固定运行时间（用于复现）")
    run.add_argument("--simulate-failure", action="store_true", help="模拟导出读取失败，验证快照兜底")
    run.add_argument("--strict", action="store_true", help="工具侧验收有 FAIL 时返回 4")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    status_cmd = sub.add_parser("status", help="查看运行状态")
    status_cmd.add_argument("--run-dir", default=DEFAULT_RUN)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(func=cmd_status)

    review = sub.add_parser("review", help="提交人工确认/退回")
    review.add_argument("--run-dir", default=DEFAULT_RUN)
    review.add_argument("--ticket", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", required=True, choices=["confirm", "return"])
    review.add_argument("--note", default="")
    review.add_argument("--item", action="append", help="已确认项，可重复")
    review.add_argument("--config", default="config/default.yaml")
    review.add_argument("--now", default=None)
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=cmd_review)

    feedback = sub.add_parser("feedback", help="登记误报 / 漏报 / 未确认（f55）")
    feedback.add_argument("--run-dir", default=DEFAULT_RUN)
    feedback.add_argument("--ticket", required=True)
    feedback.add_argument("--kind", required=True, choices=["误报", "漏报", "未确认", "其他"])
    feedback.add_argument("--reporter", required=True)
    feedback.add_argument("--note", required=True)
    feedback.add_argument("--config", default="config/default.yaml")
    feedback.add_argument("--now", default=None)
    feedback.add_argument("--json", action="store_true")
    feedback.set_defaults(func=cmd_feedback)

    for name, func, help_text in (
        ("pause", cmd_pause, "暂停试点（审核方权限）"),
        ("resume", cmd_resume, "恢复试点（审核方权限）"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--run-dir", default=DEFAULT_RUN)
        cmd.add_argument("--actor", required=True)
        cmd.add_argument("--reason", default="审核方要求暂停")
        cmd.add_argument("--config", default="config/default.yaml")
        cmd.add_argument("--now", default=None)
        cmd.set_defaults(func=func)

    report = sub.add_parser("report", help="从运行目录重跑并更新报告")
    report.add_argument("--run-dir", default=DEFAULT_RUN)
    report.add_argument("--actor", default=None)
    report.add_argument("--now", default=None)
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    audit = sub.add_parser("audit", help="审计链校验与查看")
    audit.add_argument("action", choices=["verify", "show"])
    audit.add_argument("--run-dir", default=DEFAULT_RUN)
    audit.add_argument("--ticket", default=None)
    audit.add_argument("--limit", type=int, default=20)
    audit.add_argument("--actor", default=None)
    audit.add_argument("--now", default=None)
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_audit)

    sample = sub.add_parser("sample", help="生成确定性抽查计划")
    sample.add_argument("--run-dir", default=DEFAULT_RUN)
    sample.add_argument("--size", type=int, default=80)
    sample.add_argument("--json", action="store_true")
    sample.set_defaults(func=cmd_sample)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PausedError as exc:
        print(f"试点已暂停：{exc}", file=sys.stderr)
        return exc.exit_code
    except ReviewRejected as exc:
        print(f"确认被拒绝：{exc}", file=sys.stderr)
        return exc.exit_code
    except BlockedAction as exc:
        print(f"已阻止：{exc}", file=sys.stderr)
        return exc.exit_code
    except (InputError, ConfigError, TraceError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)
    except FileNotFoundError as exc:
        print(f"错误：找不到文件 {exc.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
