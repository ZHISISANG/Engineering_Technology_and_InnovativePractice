"""试点指标：工时、返工、确认完整率三项并列，禁止用单一效率指标解释效果。

定义写死在这里，报告里同步给出公式，避免“各团队各算一套”（f51、f53）：
- 处理工时：台账登记的处理时长，统一换算为分钟；回填样本单独列出，不混入干净口径。
- 返工次数：被退回的确认记录数 + 同一工单出现多次确认的次数（重复确认视为返工信号）。
- 确认完整率：已确认的必确认项数 ÷ （工单数 × 必确认项数）。
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from expo_trace.core.audit import AuditLog
from expo_trace.core.config import Config
from expo_trace.core.models import (
    Disposition,
    Draft,
    Issue,
    QueueItem,
    Resolution,
    Ticket,
)
from expo_trace.core.store import RunStore

METRIC_LABELS = {
    "handling_minutes": "处理工时（分钟）",
    "rework_count": "返工次数",
    "confirmation_completeness": "确认完整率",
}


def sample_plan(tickets: list[Ticket], size: int, seed: int = 20261023) -> list[str]:
    """确定性分层抽样：按问题类别轮转抽取，结果可复现，便于复核同一批样本。"""
    if size <= 0 or not tickets:
        return []
    groups: dict[str, list[Ticket]] = {}
    for ticket in sorted(tickets, key=lambda t: (t.category, t.ticket_id)):
        groups.setdefault(ticket.category, []).append(ticket)
    ordered: list[Ticket] = []
    keys = sorted(groups)
    while any(groups[k] for k in keys) and len(ordered) < size:
        for key in keys:
            if groups[key] and len(ordered) < size:
                ordered.append(groups[key].pop(0))
    # seed 参与排序，仅用于让抽样顺序在相同数据下稳定、又不至于永远取前 N 条
    ordered.sort(key=lambda t: ((seed * 7919 + sum(map(ord, t.ticket_id))) % 104729, t.ticket_id))
    return [t.ticket_id for t in ordered[:size]]


def compute(
    tickets: list[Ticket],
    resolutions: dict[str, Resolution],
    drafts: dict[str, Draft],
    dispositions: dict[str, Disposition],
    queue: list[QueueItem],
    issues: list[Issue],
    reviews: list[dict[str, Any]],
    config: Config,
    store: RunStore,
    paused: bool,
    now: datetime,
) -> dict[str, Any]:
    total = len(tickets)
    counts = {d.value: 0 for d in Disposition}
    for disposition in dispositions.values():
        counts[disposition.value] = counts.get(disposition.value, 0) + 1

    with_handling = [t for t in tickets if t.handling_minutes is not None]
    clean_handling = [t for t in with_handling if not t.backfilled]
    minutes = [int(t.handling_minutes or 0) for t in with_handling]
    clean_minutes = [int(t.handling_minutes or 0) for t in clean_handling]
    handling = {
        "total_minutes": sum(minutes),
        "clean_total_minutes": sum(clean_minutes),
        "samples": len(with_handling),
        "coverage": round(len(with_handling) / total, 4) if total else 0.0,
        "median_minutes": statistics.median(minutes) if minutes else None,
        "backfilled_excluded": len(with_handling) - len(clean_handling),
    }

    per_ticket: dict[str, int] = {}
    for review in reviews:
        ticket_id = str(review.get("ticket_id", ""))
        per_ticket[ticket_id] = per_ticket.get(ticket_id, 0) + 1
    returns = [r for r in reviews if r.get("decision") == "return"]
    repeated = [tid for tid, n in per_ticket.items() if n > 1]
    rework = {
        "returned": len(returns),
        "repeated_reviews": len(repeated),
        "total": len(returns) + len(repeated),
    }

    required = [str(x) for x in config.get("review.required_confirmations", [])]
    confirmations = [r for r in reviews if r.get("decision") == "confirm"]
    covered = 0
    for review in confirmations:
        covered += len(set(str(x) for x in review.get("confirmed_items", [])) & set(required))
    denominator = total * len(required) if required else 0
    completeness = {
        "confirmed_reviews": len(confirmations),
        "covered_items": covered,
        "required_items": denominator,
        "rate": round(covered / denominator, 4) if denominator else 0.0,
    }

    queue_by_reason: dict[str, int] = {}
    for item in queue:
        for reason in item.reasons:
            queue_by_reason[reason] = queue_by_reason.get(reason, 0) + 1

    errors = [i for i in issues if str(i.severity) == "error"]
    warnings = [i for i in issues if str(i.severity) == "warn"]
    audit = AuditLog(store, actor=config.actor).verify()

    feedback = store.read_jsonl("feedback.jsonl")
    feedback_by_kind: dict[str, int] = {}
    for row in feedback:
        label = str(row.get("kind_label") or row.get("kind") or "其他")
        feedback_by_kind[label] = feedback_by_kind.get(label, 0) + 1

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "paused": paused,
        "tickets": total,
        "dispositions": counts,
        "drafts": len([d for d in drafts.values() if d.status is Disposition.DRAFT_READY]),
        "blocked_irreversible": len([d for d in drafts.values() if d.requires_authorization]),
        "watermarked": len([d for d in drafts.values() if d.body.startswith(config.watermark)]),
        "handling": handling,
        "rework": rework,
        "confirmation": completeness,
        "queue_total": len(queue),
        "queue_by_reason": dict(sorted(queue_by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "issues": {"errors": len(errors), "warnings": len(warnings), "total": len(issues)},
        "feedback": {
            "total": len(feedback),
            "by_kind": dict(sorted(feedback_by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
            "items": feedback[-5:],
        },
        "reviews": len(reviews),
        "audit": audit,
        "sample_size": int(config.get("quality.sample_size", 80)),
        "sample_plan": sample_plan(tickets, int(config.get("quality.sample_size", 80))),
        "formulas": {
            "handling_minutes": "Σ 台账处理时长（统一换算为分钟）；回填样本单列，不混入干净口径",
            "rework_count": "退回次数 + 同一工单重复确认次数",
            "confirmation_completeness": "已确认必确认项数 ÷ (工单数 × 必确认项数)",
        },
    }
