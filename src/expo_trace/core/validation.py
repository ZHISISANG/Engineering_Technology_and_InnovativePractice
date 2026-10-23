"""质量校验：把“脏数据”变成可追到原行的问题记录。

四类已知脏数据（f33）：重复编号（导入阶段已记录）、空时间、版本断链、单位差异。
另外两类必须区分的状态：**缺失**与**未确认**——前者是没填，后者是填了但没确认，
两者不能合并成空值（f31）。交接与授权范围也在这里核对。
"""

from __future__ import annotations

from datetime import datetime

from expo_trace.core.codes import Code
from expo_trace.core.config import Config
from expo_trace.core.ingest import pick
from expo_trace.core.models import FieldState, IngestResult, Issue, Resolution
from expo_trace.core.versioning import duplicate_versions


def _issue(
    code: str,
    ticket_id: str | None,
    field: str,
    detail: str,
    source: str = "",
    line: int = 0,
    raw: dict | None = None,
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


def _state_issue(ticket, field: str, state: FieldState, raw: str) -> Issue | None:
    if state is FieldState.OK:
        return None
    code = {
        FieldState.MISSING: Code.TIME_MISSING,
        FieldState.UNCONFIRMED: Code.TIME_UNCONFIRMED,
        FieldState.INVALID: Code.TIME_INVALID,
    }.get(state, Code.TIME_INVALID)
    label = {
        FieldState.MISSING: "为空（没填）",
        FieldState.UNCONFIRMED: "标记为未确认（不能当空值处理）",
        FieldState.INVALID: "格式无法解析",
    }.get(state, "状态未知")
    return _issue(code, ticket.ticket_id, field, f"{field} {label}，原值：{raw!r}", ticket.source, ticket.line, ticket.raw)


def run_checks(
    ingest_result: IngestResult,
    config: Config,
    resolutions: dict[str, Resolution],
    now: datetime,
) -> list[Issue]:
    issues: list[Issue] = list(ingest_result.issues)
    unconfirmed_tokens = {str(x).strip().lower() for x in config.get("quality.unconfirmed_tokens", [])}
    backfill_hours = float(config.get("quality.backfill_hours", 8))

    for ticket in ingest_result.tickets:
        # 时间：业务发生时间与系统接收时间分别核
        for field, value in (("business_occurred_at", ticket.occurred), ("system_received_at", ticket.received)):
            state_issue = _state_issue(ticket, field, FieldState(value.state), value.raw)
            if state_issue:
                issues.append(state_issue)

        if ticket.occurred.ok and ticket.received.ok and ticket.backfill_gap is not None:
            if ticket.backfill_gap < 0:
                issues.append(
                    _issue(
                        Code.TIME_INVALID,
                        ticket.ticket_id,
                        "system_received_at",
                        f"系统接收时间早于业务发生时间（相差 {ticket.backfill_gap} 小时），时间顺序不成立",
                        ticket.source,
                        ticket.line,
                        ticket.raw,
                    )
                )
            elif ticket.backfill_gap > backfill_hours:
                issues.append(
                    _issue(
                        Code.BACKFILLED_TIME,
                        ticket.ticket_id,
                        "system_received_at",
                        (
                            f"业务发生时间与系统接收时间相差 {ticket.backfill_gap} 小时，"
                            "属于“先群消息、后补填”的回填样本；耗时统计须用发生时间，不能直接用回填时间"
                        ),
                        ticket.source,
                        ticket.line,
                        ticket.raw,
                    )
                )

        # 关键字段：缺失与未确认分开
        for field, value in (
            ("batch", ticket.batch),
            ("category", ticket.category),
            ("assignee", ticket.assignee),
        ):
            text = (value or "").strip()
            if not text:
                issues.append(
                    _issue(Code.KEY_FIELD_MISSING, ticket.ticket_id, field, f"关键字段 {field} 为空", ticket.source, ticket.line, ticket.raw)
                )
            elif text.lower() in unconfirmed_tokens:
                issues.append(
                    _issue(
                        Code.KEY_FIELD_UNCONFIRMED,
                        ticket.ticket_id,
                        field,
                        f"关键字段 {field} 标记为未确认（原值 {text!r}），不能当作缺失值处理",
                        ticket.source,
                        ticket.line,
                        ticket.raw,
                    )
                )

        # 单位差异：不静默换算，保留说明与原行
        if ticket.handling_note:
            issues.append(
                _issue(
                    Code.UNIT_DIFFERENCE,
                    ticket.ticket_id,
                    "handling",
                    f"处理时长单位口径需核对：{ticket.handling_note}",
                    "ledger.csv",
                    0,
                    ticket.raw,
                )
            )

        # 交接记录
        handoff = ticket.handoff
        if handoff is None:
            issues.append(
                _issue(
                    Code.HANDOFF_MISSING,
                    ticket.ticket_id,
                    "handoff",
                    "没有找到该工单的交接记录（接收人/版本/未结项都不可核）",
                    "handoff.csv",
                    0,
                    {},
                )
            )
        else:
            if handoff.note_only:
                issues.append(
                    _issue(
                        Code.HANDOFF_NOTE_ONLY,
                        ticket.ticket_id,
                        "handoff.note",
                        "交接只写了“已处理/已转”，没有列明最新版本、未确认事项与截止时间",
                        handoff.source,
                        handoff.line,
                        handoff.raw,
                    )
                )
            owner_raw, _ = pick(handoff.raw, list(config.mapping("handoff").get("unconfirmed_owner", ["unconfirmed_owner", "未确认项归属"])))
            if handoff.unconfirmed_items and owner_raw and handoff.to_role and owner_raw == handoff.to_role:
                issues.append(
                    _issue(
                        Code.UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY,
                        ticket.ticket_id,
                        "handoff.unconfirmed_owner",
                        (
                            f"未确认项“{'、'.join(handoff.unconfirmed_items)}”被归到接收方 {handoff.to_role} 名下；"
                            "未确认项在确认前必须仍由交出方负责"
                        ),
                        handoff.source,
                        handoff.line,
                        handoff.raw,
                    )
                )
            resolution = resolutions.get(ticket.ticket_id)
            if handoff.latest_version and resolution and resolution.version_id:
                if handoff.latest_version != resolution.version_id:
                    issues.append(
                        _issue(
                            Code.VERSION_DRIFT,
                            ticket.ticket_id,
                            "handoff.latest_version",
                            (
                                f"交接记录写的是 {handoff.latest_version}，当前版本判定为 {resolution.version_id}；"
                                "交接双方须按原始记录核对，不能各用一张汇总表"
                            ),
                            handoff.source,
                            handoff.line,
                            handoff.raw,
                        )
                    )

        resolution = resolutions.get(ticket.ticket_id)
        if resolution and resolution.state.value != "current" and resolution.reason_code:
            issues.append(
                _issue(
                    resolution.reason_code,
                    ticket.ticket_id,
                    "version",
                    resolution.reason,
                    "file_index.csv",
                    0,
                    {},
                )
            )
        if resolution and resolution.chain_break and resolution.reason_code != Code.VERSION_CHAIN_BREAK:
            issues.append(
                _issue(
                    Code.VERSION_CHAIN_BREAK,
                    ticket.ticket_id,
                    "version.supersedes",
                    resolution.reason,
                    "file_index.csv",
                    0,
                    {},
                )
            )

    # 授权范围：导出清单没有声明批次或用途，不能假定可以处理
    scope = ingest_result.export_meta.get("authorized_scope") or {}
    batches = [str(x) for x in scope.get("batches", [])]
    purpose = str(scope.get("purpose", "")).strip()
    if not ingest_result.export_meta:
        issues.append(
            _issue(Code.SOURCE_INCOMPLETE, None, "manifest", "缺少导出清单 manifest.json，授权范围与只读声明不可核", "manifest.json", 0, {})
        )
    if not batches:
        issues.append(
            _issue(Code.AUTHORIZATION_UNCLEAR, None, "authorized_scope.batches", "导出清单未声明授权批次", "manifest.json", 0, scope)
        )
    if not purpose:
        issues.append(
            _issue(Code.AUTHORIZATION_UNCLEAR, None, "authorized_scope.purpose", "导出清单未声明用途", "manifest.json", 0, scope)
        )
    for ticket in ingest_result.tickets:
        if batches and ticket.batch not in batches:
            issues.append(
                _issue(
                    Code.AUTHORIZATION_UNCLEAR,
                    ticket.ticket_id,
                    "batch",
                    f"工单批次 {ticket.batch} 不在授权批次 {batches} 内，不得处理或对外使用",
                    ticket.source,
                    ticket.line,
                    ticket.raw,
                )
            )

    # 版本记录自身
    issues.extend(duplicate_versions(ingest_result.versions))
    for version in ingest_result.versions:
        missing = [
            field
            for field, value in (
                ("version_id", version.version_id),
                ("batch", version.batch),
                ("category", version.category),
                ("effective_from", version.effective_from),
            )
            if not value
        ]
        if missing:
            issues.append(
                _issue(
                    Code.KEY_FIELD_MISSING,
                    None,
                    ",".join(missing),
                    f"版本记录缺少关键字段：{'、'.join(missing)}",
                    version.source,
                    version.line,
                    version.raw,
                )
            )

    # 稳定排序：先按工单，再按代码，便于人工逐条核对
    issues.sort(key=lambda i: (i.ticket_id or "", i.code, i.line))
    return issues


def blocking_issues(issues: list[Issue], blocking_codes: set[str]) -> dict[str, list[Issue]]:
    grouped: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.ticket_id and issue.code in blocking_codes:
            grouped.setdefault(issue.ticket_id, []).append(issue)
    return grouped
