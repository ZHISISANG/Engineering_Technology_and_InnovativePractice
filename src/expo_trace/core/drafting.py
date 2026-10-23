"""草稿生成：只组装“可追溯的审核草稿”，不替审核人下结论。

硬边界（REQ-6 / f05、f52、f32）：
- 每份草稿第一行固定“未确认 · 不可对外”标识；
- 涉及收费或参展资格的问答，草稿标记为不可直接对外，只能由授权人复核；
- 不提供发布、发送、写回的实现——对应函数一律抛 :class:`BlockedAction`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn

from expo_trace.core.codes import Code
from expo_trace.core.config import Config
from expo_trace.core.models import (
    Disposition,
    Draft,
    Issue,
    KnowledgeVersion,
    Resolution,
    Ticket,
)
from expo_trace.core.routing import latest_reviews
from expo_trace.utils.errors import BlockedAction
from expo_trace.utils.textutil import contains_any
from expo_trace.utils.timeutil import iso

FORBIDDEN_ACTIONS = [
    "自动向展商发布或发送任何内容",
    "作出收费、报价、折扣或参展资格决定",
    "写回服务台/台账/文件库",
    "调用外部消息或执行接口",
]
BASE_UNCONFIRMED = [
    "该规则是否适用于本展商的具体情形（需人工核对展位/批次/合同）",
    "草稿措辞是否构成对外承诺（须业务人员确认）",
]


def _version_by_id(versions: list[KnowledgeVersion]) -> dict[str, KnowledgeVersion]:
    return {v.version_id: v for v in versions}


def _requires_authorization(ticket: Ticket, version: KnowledgeVersion | None, config: Config) -> bool:
    categories = {str(x) for x in config.get("safety.irreversible_categories", [])}
    keywords = [str(x) for x in config.get("safety.irreversible_keywords", [])]
    if ticket.category in categories:
        return True
    text = " ".join(filter(None, [ticket.question, version.statement if version else ""]))
    return bool(contains_any(text, keywords))


def _header(ticket: Ticket) -> list[str]:
    return [
        f"**工单**：{ticket.ticket_id}（展会批次 {ticket.batch or '未填'} · 问题类别 {ticket.category or '未填'}）",
        f"**处理人**：{ticket.assignee or '未记录'}",
        f"**业务发生时间**：{iso(ticket.occurred_at) or '缺失/未确认'}",
        f"**系统接收时间**：{iso(ticket.received_at) or '缺失/未确认'}"
        + (f"（与发生时间相差 {ticket.backfill_gap} 小时，属回填样本）" if ticket.backfilled else ""),
        f"**问题摘要**：{ticket.question or '（未填写）'}",
    ]


def _source_rows(ticket: Ticket, version: KnowledgeVersion | None) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [
        {"kind": "ticket", "source": ticket.source, "line": ticket.line, "ref": ticket.ticket_id}
    ]
    if version is not None:
        sources.append(
            {
                "kind": "version",
                "source": version.source,
                "line": version.line,
                "ref": version.version_id,
                "notice_id": version.notice_id,
                "effective_from": iso(version.effective_from),
                "effective_to": iso(version.effective_to),
                "confirmed_by": version.confirmed_by,
                "file_name": version.file_name,
            }
        )
    return sources


def build_draft(
    ticket: Ticket,
    resolution: Resolution,
    versions: list[KnowledgeVersion],
    blocking_issues: list[Issue],
    config: Config,
    review: dict[str, Any] | None,
    paused: bool,
    now: datetime,
) -> Draft:
    watermark = config.watermark
    mapping = _version_by_id(versions)
    version = mapping.get(resolution.version_id or "")
    requires_auth = _requires_authorization(ticket, version, config)
    unconfirmed = list(BASE_UNCONFIRMED)
    if ticket.backfilled:
        unconfirmed.append("接收时间明显晚于业务发生时间，耗时与时效判断需按发生时间重算")
    if requires_auth:
        unconfirmed.append("涉及收费或参展资格口径，须由授权人员确认后方可用于人工回复")

    if paused:
        return Draft(
            ticket_id=ticket.ticket_id,
            status=Disposition.HUMAN_REVIEW,
            watermark=watermark,
            subject=f"【已暂停】{ticket.ticket_id} 草稿生成已停止",
            body="\n".join(
                [
                    watermark,
                    "",
                    "**试点已暂停，不生成新的回复草稿。**",
                    "暂停期间已生成的草稿一律不得对外使用；恢复须由业务负责人或审核与质量负责人操作并留痕。",
                ]
            ),
            version_id=resolution.version_id,
            sources=_source_rows(ticket, version),
            blocked_actions=list(FORBIDDEN_ACTIONS),
            requires_authorization=requires_auth,
            unconfirmed_items=unconfirmed,
            created_at=now,
            statement=version.statement if version else "",
        )

    if not resolution.locatable or version is None:
        body = [
            watermark,
            "",
            "**本工单不生成回复草稿：当前版本无法判定。**",
            "",
            f"**原因**：{resolution.reason}",
            "",
            "**已核查的版本**：",
        ]
        if resolution.considered:
            for item in resolution.considered[:8]:
                body.append(
                    f"- {item.get('version_id')}（{item.get('state')}）：{item.get('why')}"
                )
        else:
            body.append("- 无可用版本记录")
        body.extend(
            [
                "",
                "**需要补充/确认**：确认该时点应适用的版本，并由责任人在交接清单上登记。",
                "",
                "**禁止动作**：不得凭文件名、群消息或“最新版”字样对外答复。",
            ]
        )
        return Draft(
            ticket_id=ticket.ticket_id,
            status=Disposition.BLOCKED if requires_auth else Disposition.HUMAN_REVIEW,
            watermark=watermark,
            subject=f"【不能出稿】{ticket.ticket_id} 版本无法判定",
            body="\n".join(body),
            version_id=None,
            sources=_source_rows(ticket, None),
            blocked_actions=list(FORBIDDEN_ACTIONS),
            requires_authorization=requires_auth,
            unconfirmed_items=unconfirmed,
            created_at=now,
            statement="",
        )

    status = Disposition.BLOCKED if (requires_auth or blocking_issues) else Disposition.DRAFT_READY
    body = [watermark, "", f"**主题**：{ticket.batch} {ticket.category} 咨询回复草稿（待人工确认）", ""]
    body.extend(_header(ticket))
    body.extend(
        [
            "",
            f"**依据版本**：{version.version_id}（通知 {version.notice_id or '未编号'}）",
            f"- 有效期：{iso(version.effective_from)} ~ {iso(version.effective_to) or '开放'}",
            f"- 确认状态：{'已确认' if version.confirmed else '未确认'}"
            + (f"，确认人 {version.confirmed_by}" if version.confirmed_by else ""),
            f"- 规则摘要：{version.statement or '（该版本没有登记摘要，需人工查阅原文）'}",
            "",
            f"**判定说明**：{resolution.reason}；排序依据为{resolution.basis}。",
        ]
    )
    excluded = [
        item for item in resolution.considered if item.get("version_id") != version.version_id
    ]
    if excluded:
        body.append("")
        body.append("**已排除的版本**（文件名、接收时间均不作为判定依据）：")
        for item in excluded[:5]:
            body.append(f"- {item.get('version_id')}：{item.get('why')}")
    if ticket.backfilled:
        body.append("- 该工单为回填样本：回填时间不作为业务发生时间。")
    if unconfirmed:
        body.append("")
        body.append("**未确认事项**：")
        for index, item in enumerate(unconfirmed, 1):
            body.append(f"{index}. {item}")
    if blocking_issues:
        body.append("")
        body.append("**阻断项**（必须先处理，不能直接出稿）：")
        for issue in blocking_issues:
            body.append(f"- [{issue.code}] {issue.detail}")
    if requires_auth:
        body.extend(
            [
                "",
                "**不可逆边界**：本问答涉及收费或参展资格，只能作为内部审核稿；"
                "对外口径须由业务负责人/审核与质量负责人确认，系统不得自动发布。",
            ]
        )
    if review and review.get("decision") == "confirm":
        body.append(
            f"\n**人工确认**：{review.get('reviewer')} 于 {review.get('created_at')} 确认"
            "（仅确认草稿与来源，不等于对外授权）。"
        )
    body.extend(["", "**来源**："])
    for source in _source_rows(ticket, version):
        body.append(f"- {source['source']} 第 {source['line']} 行 → {source['ref']}")
    body.extend(["", "**禁止动作**：" + "；".join(FORBIDDEN_ACTIONS) + "。"])

    return Draft(
        ticket_id=ticket.ticket_id,
        status=status,
        watermark=watermark,
        subject=f"【草稿】{ticket.category} 咨询回复 · {ticket.ticket_id}",
        body="\n".join(body),
        version_id=version.version_id,
        sources=_source_rows(ticket, version),
        blocked_actions=list(FORBIDDEN_ACTIONS),
        requires_authorization=requires_auth,
        unconfirmed_items=unconfirmed,
        created_at=now,
        statement=version.statement,
    )


def build_drafts(
    tickets: list[Ticket],
    resolutions: dict[str, Resolution],
    versions: list[KnowledgeVersion],
    issues: list[Issue],
    config: Config,
    reviews: list[dict[str, Any]],
    paused: bool,
    now: datetime,
) -> dict[str, Draft]:
    latest = latest_reviews(reviews)
    blocking_codes = config.blocking_codes()
    by_ticket: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.ticket_id and issue.code in blocking_codes:
            by_ticket.setdefault(issue.ticket_id, []).append(issue)
    return {
        ticket.ticket_id: build_draft(
            ticket,
            resolutions[ticket.ticket_id],
            versions,
            by_ticket.get(ticket.ticket_id, []),
            config,
            latest.get(ticket.ticket_id),
            paused,
            now,
        )
        for ticket in tickets
    }


def publish(*_args: Any, **_kwargs: Any) -> NoReturn:
    """占位即拒绝：本工具没有对外发布能力，也不打算有。"""
    raise BlockedAction("禁止对外发布：本工具只生成审核草稿，对外承诺必须由业务人员完成")


def send_external(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise BlockedAction("禁止发送外部消息：试点不开放外部消息接口")


def write_back(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise BlockedAction("禁止写回源系统：试点只开放脱敏只读导出")
