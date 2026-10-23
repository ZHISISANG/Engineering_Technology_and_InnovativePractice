"""误报 / 漏报 / 未确认登记。

REQ-5 的验收不只要求三项指标并列，还要求每周复盘误报、漏报与未确认项（f55）。
工具不能替人判断哪条是误报，但必须让人**有地方登记**，并把登记写进审计链与周报。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from expo_trace.core.audit import AuditLog
from expo_trace.core.config import Config
from expo_trace.core.store import RunStore
from expo_trace.utils.errors import ReviewRejected
from expo_trace.utils.textutil import stable_id
from expo_trace.utils.timeutil import iso

KINDS: dict[str, str] = {
    "误报": "false_positive",
    "漏报": "false_negative",
    "未确认": "unconfirmed",
    "其他": "other",
}
KIND_LABELS: dict[str, str] = {value: key for key, value in KINDS.items()}


def submit_feedback(
    store: RunStore,
    config: Config,
    *,
    ticket_id: str,
    kind: str,
    reporter: str,
    note: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """登记一条误报/漏报/未确认，返回写入的记录。

    只做登记，不自动改变工单状态：是否调整判定仍由人决定。
    """

    kind_key = KINDS.get(kind, kind)
    if kind_key not in KIND_LABELS:
        raise ReviewRejected("登记类型只能是：误报、漏报、未确认、其他")
    if not ticket_id.strip():
        raise ReviewRejected("必须指定工单编号")
    if not reporter.strip():
        raise ReviewRejected("必须填写登记人")
    if len(note.strip()) < 4:
        raise ReviewRejected("请写清具体问题（至少 4 个字），否则周会无法复盘")

    now = now or datetime.now().astimezone()
    record: dict[str, Any] = {
        "feedback_id": stable_id("FB", ticket_id, reporter, iso(now), note),
        "ticket_id": ticket_id.strip(),
        "kind": kind_key,
        "kind_label": KIND_LABELS[kind_key],
        "reporter": reporter.strip(),
        "note": note.strip(),
        "created_at": iso(now),
        "actor": config.actor,
    }
    store.append_jsonl("feedback.jsonl", record)
    AuditLog(store, actor=reporter.strip()).append(
        "feedback_submitted",
        record["ticket_id"],
        {"kind": kind_key, "note": record["note"]},
        at=now,
    )
    return record
