"""人工确认：把“谁确认、确认了哪一版、确认了哪些项”写进只追加记录。

拒绝的情形要具体，不能只说“失败”：
- 确认人不在授权名单；
- 草稿仍处于“版本无法判定”状态；
- 确认项没覆盖必需清单；
- 收费/资格类草稿由非授权角色确认；
- 同一秒内重复提交同一条确认（防误触重复计数）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from expo_trace.core.audit import AuditLog
from expo_trace.core.config import Config
from expo_trace.core.models import Draft, ReviewDecision, ReviewRecord
from expo_trace.core.store import RunStore
from expo_trace.utils.errors import ReviewRejected
from expo_trace.utils.textutil import sha256_bytes, stable_id
from expo_trace.utils.timeutil import iso


def _record_dict(record: ReviewRecord) -> dict[str, Any]:
    return {
        "review_id": record.review_id,
        "ticket_id": record.ticket_id,
        "reviewer": record.reviewer,
        "decision": record.decision.value,
        "note": record.note,
        "version_id": record.version_id,
        "confirmed_items": record.confirmed_items,
        "created_at": iso(record.created_at),
        "draft_digest": record.draft_digest,
    }


def submit_review(
    store: RunStore,
    config: Config,
    *,
    ticket_id: str,
    reviewer: str,
    decision: str,
    note: str,
    confirmed_items: list[str],
    draft: Draft,
    now: datetime,
) -> dict[str, Any]:
    if reviewer not in config.reviewers():
        raise ReviewRejected(
            f"确认人 {reviewer!r} 不在授权名单（{('、'.join(config.reviewers()))}）中，不能执行确认"
        )
    try:
        decision_enum = ReviewDecision(decision)
    except ValueError as exc:
        raise ReviewRejected("确认结论只能是 confirm 或 return") from exc

    required = [str(x) for x in config.get("review.required_confirmations", [])]
    if decision_enum is ReviewDecision.CONFIRM:
        if draft.status.value == "human_review" and not draft.version_id:
            raise ReviewRejected("该工单的版本仍无法判定，不能直接确认；请先补证并重新运行")
        missing = [item for item in required if item not in confirmed_items]
        if missing:
            raise ReviewRejected(f"确认项不完整，还缺：{'、'.join(missing)}")
        if draft.requires_authorization:
            authorized = [str(x) for x in config.get("safety.irreversible_reviewers", [])]
            if reviewer not in authorized:
                raise ReviewRejected(
                    f"该草稿涉及收费或参展资格，只有 {'、'.join(authorized)} 可以确认，{reviewer} 无权确认"
                )
    else:
        if len((note or "").strip()) < 4:
            raise ReviewRejected("退回必须写明原因（至少 4 个字），便于返工统计与复盘")

    review_id = stable_id("RV", ticket_id, reviewer, iso(now))
    existing = {row.get("review_id") for row in store.reviews()}
    if review_id in existing:
        raise ReviewRejected("重复提交：该确认记录已存在，未重复写入")

    record = ReviewRecord(
        review_id=review_id,
        ticket_id=ticket_id,
        reviewer=reviewer,
        decision=decision_enum,
        note=(note or "").strip(),
        version_id=draft.version_id or "",
        confirmed_items=list(confirmed_items),
        created_at=now,
        draft_digest=sha256_bytes(draft.body.encode("utf-8")),
    )
    payload = _record_dict(record)
    store.append_review(payload)
    AuditLog(store, actor=reviewer).append(
        "review_submitted",
        ticket_id,
        {
            "review_id": review_id,
            "decision": decision_enum.value,
            "version_id": record.version_id,
            "confirmed_items": list(confirmed_items),
            "draft_digest": record.draft_digest,
        },
        at=now,
    )
    return payload
