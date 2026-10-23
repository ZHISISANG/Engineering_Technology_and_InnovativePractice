"""版本判定：只凭有效期与确认状态判断“当前版本”，不看文件名。

规则直接来自需求假设 REQ-1（f24）与 REQ-3（f34）：

- 判定只用有效期区间是否覆盖``业务发生时间``，以及版本是否已确认、是否作废；
  文件名、修改时间不参与排序——这正是“最新版_xxx.pdf”不能作为依据的原因。
- 作废记录保留作废原因，作废版本不能成为当前版本。
- 多个已确认版本同时覆盖同一时点时，不猜：进入“无法判定”，转人工队列。
- 版本链（supersedes）断链时同样不能默认通过。
- 后到但未确认的版本，不得覆盖已确认版本（f34）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from expo_trace.core.codes import Code
from expo_trace.core.models import (
    Issue,
    KnowledgeVersion,
    Resolution,
    Ticket,
    VersionState,
)
from expo_trace.utils.timeutil import iso

ORDERING_BASIS = "有效期覆盖 + 确认状态 > 作废状态 > 生效起始 > 确认时间 > 接收时间；不使用文件名/修改时间"
_MAX_CHAIN = 20


def _by_id(versions: Iterable[KnowledgeVersion]) -> dict[str, KnowledgeVersion]:
    return {v.version_id: v for v in versions if v.version_id}


def _missing_link(version: KnowledgeVersion, mapping: dict[str, KnowledgeVersion]) -> str:
    """沿 supersedes 向上查找；返回第一个缺失的版本号，链路完整时返回空串。"""
    node = version
    for _ in range(_MAX_CHAIN):
        target = (node.supersedes or "").strip()
        if not target:
            return ""
        if target not in mapping:
            return target
        node = mapping[target]
    return "版本链超过可追溯深度"


def _superseded_by(old: KnowledgeVersion, new: KnowledgeVersion, mapping: dict[str, KnowledgeVersion]) -> bool:
    node: KnowledgeVersion | None = new
    for _ in range(_MAX_CHAIN):
        target = (node.supersedes or "").strip() if node else ""
        if not target:
            return False
        if target == old.version_id:
            return True
        node = mapping.get(target)
    return False


def _rank(version: KnowledgeVersion) -> tuple:
    # 注意：这里刻意不包含 file_name / mtime；增加它们会重演“按文件名猜最新版”的错误。
    return (
        1 if version.confirmed else 0,
        0 if version.void else 1,
        version.effective_from,
        version.confirmed_at or datetime.min.replace(tzinfo=version.effective_from.tzinfo),
        version.received_at or datetime.min.replace(tzinfo=version.effective_from.tzinfo),
        version.version_id,
    )


def _why(version: KnowledgeVersion, moment: datetime) -> str:
    if version.void:
        return f"已作废：{version.void_reason or '未填原因'}"
    if not version.covers(moment):
        return "有效期不覆盖业务发生时间"
    if not version.confirmed:
        return "版本未确认"
    return "有效期覆盖且已确认"


def resolve_ticket(
    ticket: Ticket,
    versions: list[KnowledgeVersion],
    unconfirmed_tokens: tuple[str, ...] = (),
) -> Resolution:
    moment = ticket.occurred_at or ticket.received_at
    if moment is None:
        return Resolution(
            ticket_id=ticket.ticket_id,
            state=VersionState.UNCONFIRMED,
            version_id=None,
            reason="业务发生时间与系统接收时间都不可用，无法判断应适用哪一版规则",
            reason_code=Code.KEY_FIELD_MISSING,
        )

    mapping = _by_id(versions)
    pool = [v for v in versions if v.batch == ticket.batch and v.category == ticket.category]
    covering = [v for v in pool if v.covers(moment)]
    considered = [
        {
            "version_id": v.version_id,
            "notice_id": v.notice_id,
            "state": "void" if v.void else ("confirmed" if v.confirmed else "unconfirmed"),
            "effective_from": iso(v.effective_from),
            "effective_to": iso(v.effective_to),
            "why": _why(v, moment),
        }
        for v in (covering or pool)
    ]

    if covering:
        # 把“不覆盖该时点”的版本也留进证据：文件名再新、接收再晚，只要有效期不覆盖就不参与判定。
        for rejected in [v for v in pool if not v.covers(moment)][:5]:
            considered.append(
                {
                    "version_id": rejected.version_id,
                    "notice_id": rejected.notice_id,
                    "state": "void" if rejected.void else ("confirmed" if rejected.confirmed else "unconfirmed"),
                    "effective_from": iso(rejected.effective_from),
                    "effective_to": iso(rejected.effective_to),
                    "why": _why(rejected, moment) + "（不参与判定）",
                }
            )

    if not covering:
        expired = [v for v in pool if v.effective_to is not None and v.effective_to < moment]
        if expired:
            latest = max(expired, key=lambda v: v.effective_to or v.effective_from)
            detail = (
                f"业务发生时间 {iso(moment)} 不在任何已知版本的有效期内；"
                f"最近过期版本 {latest.version_id} 截止 {iso(latest.effective_to)}"
            )
            if latest.void_reason:
                detail += f"，作废原因：{latest.void_reason}"
        else:
            detail = f"业务发生时间 {iso(moment)} 之前没有任何该批次/类别的版本记录"
        return Resolution(
            ticket_id=ticket.ticket_id,
            state=VersionState.EXPIRED,
            version_id=None,
            reason=detail,
            reason_code=Code.NO_CURRENT_VERSION,
            considered=considered,
        )

    active = [v for v in covering if not v.void]
    confirmed = [v for v in active if v.confirmed]
    voids = [v for v in covering if v.void]

    if not confirmed:
        if not active:
            reason = "只存在已作废版本：" + "；".join(
                f"{v.version_id}（{v.void_reason or '未填原因'}）" for v in voids
            )
            return Resolution(
                ticket_id=ticket.ticket_id,
                state=VersionState.EXPIRED,
                version_id=None,
                reason=reason,
                reason_code=Code.NO_CURRENT_VERSION,
                considered=considered,
            )
        candidates = sorted(active, key=_rank, reverse=True)
        return Resolution(
            ticket_id=ticket.ticket_id,
            state=VersionState.UNCONFIRMED,
            version_id=None,
            reason=(
                f"覆盖该时点的 {len(candidates)} 个版本均未确认（候选："
                + "、".join(v.version_id for v in candidates)
                + "），未确认版本不能作为回复依据"
            ),
            reason_code=Code.KEY_FIELD_UNCONFIRMED,
            considered=considered,
        )

    # 已确认版本中，如果旧版本被新版本明确替代，旧版本退出竞争
    reduced = [
        v
        for v in confirmed
        if not any(_superseded_by(v, other, mapping) for other in confirmed if other is not v)
    ]
    if len(reduced) > 1:
        return Resolution(
            ticket_id=ticket.ticket_id,
            state=VersionState.UNCONFIRMED,
            version_id=None,
            reason=(
                "多个已确认版本同时覆盖该时点，且不存在替代关系："
                + "、".join(sorted(v.version_id for v in reduced))
                + "；不能默认通过，需人工确认"
            ),
            reason_code=Code.VERSION_CONFLICT,
            considered=considered,
        )

    winner = reduced[0]
    missing = _missing_link(winner, mapping)
    if missing:
        return Resolution(
            ticket_id=ticket.ticket_id,
            state=VersionState.UNCONFIRMED,
            version_id=None,
            reason=f"版本 {winner.version_id} 的替代链指向 {missing}，该版本不存在，版本链断开",
            reason_code=Code.VERSION_CHAIN_BREAK,
            chain_break=True,
            considered=considered,
        )

    # 记录“后到但未确认”的记录：它们没有覆盖已确认版本，但要在证据里说明
    overridden = [
        v.version_id
        for v in active
        if not v.confirmed and winner.received_at and v.received_at and v.received_at > winner.received_at
    ]
    if overridden:
        by_key = {item.get("version_id"): item for item in considered}
        for version_id in overridden:
            note = "接收时间更晚但未确认，按规则不得覆盖已确认版本（f34）"
            if version_id in by_key:
                by_key[version_id]["why"] = f"{by_key[version_id]['why']}；{note}"
            else:
                considered.append({"version_id": version_id, "state": "unconfirmed", "why": note})

    return Resolution(
        ticket_id=ticket.ticket_id,
        state=VersionState.CURRENT,
        version_id=winner.version_id,
        reason=(
            f"{winner.version_id} 的有效期 {iso(winner.effective_from)}~{iso(winner.effective_to) or '开放'} "
            f"覆盖 {iso(moment)}，确认人 {winner.confirmed_by or '未记录'}"
        ),
        reason_code="",
        considered=considered,
    )


def resolve_all(
    tickets: list[Ticket],
    versions: list[KnowledgeVersion],
    unconfirmed_tokens: tuple[str, ...] = (),
) -> dict[str, Resolution]:
    return {t.ticket_id: resolve_ticket(t, versions, unconfirmed_tokens) for t in tickets}


def duplicate_versions(versions: list[KnowledgeVersion]) -> list[Issue]:
    """同一版本号被重复登记：保留原行，提示人工核对字段是否一致。"""
    grouped: dict[str, list[KnowledgeVersion]] = {}
    for version in versions:
        grouped.setdefault(version.version_id, []).append(version)
    issues: list[Issue] = []
    for version_id, rows in grouped.items():
        if len(rows) < 2:
            continue
        issues.append(
            Issue(
                code=Code.VERSION_DUPLICATE,
                ticket_id=None,
                field_name="version_id",
                detail=f"版本 {version_id} 在文件库中出现 {len(rows)} 次，需核对确认状态与有效期是否一致",
                source="file_index.csv",
                line=rows[1].line,
                raw=rows[1].raw,
                severity="warn",
            )
        )
    return issues
