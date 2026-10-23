"""分派：每条工单必须落到“可出草稿 / 进人工队列 / 不可逆边界阻塞”之一。

这里没有“默认通过”：只要存在阻断代码、版本无法判定或试点暂停，
工单就必须带原因、具名接收人和时限进入人工队列（f44、f23）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from expo_trace.core.codes import Code, title
from expo_trace.core.config import Config
from expo_trace.core.models import (
    Disposition,
    Draft,
    Issue,
    QueueItem,
    Resolution,
    Ticket,
)
from expo_trace.utils.errors import TraceError
from expo_trace.utils.timeutil import iso

DEFAULT_OWNER = "审核与质量负责人"
REASON_ORDER = [
    Code.PILOT_PAUSED,
    Code.FEE_OR_QUALIFICATION,
    Code.AUTHORIZATION_UNCLEAR,
    Code.VERSION_CONFLICT,
    Code.VERSION_CHAIN_BREAK,
    Code.NO_CURRENT_VERSION,
    Code.VERSION_DRIFT,
    Code.KEY_FIELD_MISSING,
    Code.KEY_FIELD_UNCONFIRMED,
    Code.DUPLICATE_ID,
    Code.TIME_MISSING,
    Code.TIME_INVALID,
    Code.TIME_UNCONFIRMED,
    Code.HANDOFF_NOTE_ONLY,
    Code.UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY,
    Code.SOURCE_INCOMPLETE,
    Code.REVIEW_RETURNED,
    Code.VERSION_DUPLICATE,
    Code.BACKFILLED_TIME,
    Code.UNIT_DIFFERENCE,
    Code.HANDOFF_MISSING,
]


def latest_reviews(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        ticket_id = str(review.get("ticket_id", ""))
        if ticket_id:
            latest[ticket_id] = review
    return latest


def _ordered(reasons: list[str]) -> list[str]:
    seen: list[str] = []
    for reason in REASON_ORDER:
        if reason in reasons and reason not in seen:
            seen.append(reason)
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return seen


def route(
    tickets: list[Ticket],
    resolutions: dict[str, Resolution],
    drafts: dict[str, Draft],
    issues: list[Issue],
    config: Config,
    reviews: list[dict[str, Any]],
    paused: bool,
    now: datetime,
) -> tuple[dict[str, Disposition], list[QueueItem]]:
    blocking_codes = config.blocking_codes()
    owners = config.owners()
    sla_hours = float(config.get("routing.sla_hours", 24))
    latest = latest_reviews(reviews)

    issues_by_ticket: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.ticket_id:
            issues_by_ticket.setdefault(issue.ticket_id, []).append(issue)

    dispositions: dict[str, Disposition] = {}
    queue: list[QueueItem] = []

    for ticket in tickets:
        reasons: list[str] = []
        if paused:
            reasons.append(Code.PILOT_PAUSED)

        for issue in issues_by_ticket.get(ticket.ticket_id, []):
            if issue.code in blocking_codes and issue.code not in reasons:
                reasons.append(issue.code)

        resolution = resolutions[ticket.ticket_id]
        if not resolution.locatable and resolution.reason_code and resolution.reason_code not in reasons:
            reasons.append(resolution.reason_code)

        review = latest.get(ticket.ticket_id)
        if review and review.get("decision") == "return" and Code.REVIEW_RETURNED not in reasons:
            reasons.append(Code.REVIEW_RETURNED)

        draft = drafts[ticket.ticket_id]
        if draft.requires_authorization and Code.FEE_OR_QUALIFICATION not in reasons:
            reasons.append(Code.FEE_OR_QUALIFICATION)

        reasons = _ordered(reasons)

        if draft.requires_authorization:
            disposition = Disposition.BLOCKED
        elif reasons:
            disposition = Disposition.HUMAN_REVIEW
        else:
            disposition = Disposition.DRAFT_READY
        dispositions[ticket.ticket_id] = disposition

        if disposition is Disposition.DRAFT_READY:
            continue

        owner = owners.get(reasons[0], DEFAULT_OWNER) if reasons else DEFAULT_OWNER
        if draft.requires_authorization:
            owner = owners.get(Code.FEE_OR_QUALIFICATION, "业务负责人")
        queue.append(
            QueueItem(
                ticket_id=ticket.ticket_id,
                reasons=reasons or [Code.SOURCE_INCOMPLETE],
                owner=owner,
                due_at=iso(now + timedelta(hours=sla_hours)) or "",
                requires_authorization=draft.requires_authorization,
                note="；".join(title(r) for r in reasons),
                created_at=now,
                dispositions=disposition.value,
            )
        )

    if len(dispositions) != len(tickets):
        raise TraceError("内部错误：存在未分派的工单（不允许静默通过）")
    for ticket in tickets:
        if dispositions.get(ticket.ticket_id) is None:
            raise TraceError(f"内部错误：工单 {ticket.ticket_id} 没有分派结果")
    return dispositions, queue
