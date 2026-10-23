"""问题代码、严重级别与中文标题。

问题代码同时用作人工队列的转派原因（routing.owners 的键），
保证“发现问题 → 进人工队列 → 有具名接收人”是同一条链路。
"""

from __future__ import annotations


class Code:
    # 输入与字段
    FIELD_UNMAPPED = "FIELD_UNMAPPED"
    SOURCE_INCOMPLETE = "SOURCE_INCOMPLETE"
    AUTHORIZATION_UNCLEAR = "AUTHORIZATION_UNCLEAR"
    # 时间
    TIME_MISSING = "TIME_MISSING"
    TIME_UNCONFIRMED = "TIME_UNCONFIRMED"
    TIME_INVALID = "TIME_INVALID"
    BACKFILLED_TIME = "BACKFILLED_TIME"
    # 版本
    NO_CURRENT_VERSION = "NO_CURRENT_VERSION"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    VERSION_CHAIN_BREAK = "VERSION_CHAIN_BREAK"
    VERSION_DUPLICATE = "VERSION_DUPLICATE"
    VERSION_DRIFT = "VERSION_DRIFT"
    # 数据质量
    DUPLICATE_ID = "DUPLICATE_ID"
    UNIT_DIFFERENCE = "UNIT_DIFFERENCE"
    KEY_FIELD_MISSING = "KEY_FIELD_MISSING"
    KEY_FIELD_UNCONFIRMED = "KEY_FIELD_UNCONFIRMED"
    # 交接
    HANDOFF_MISSING = "HANDOFF_MISSING"
    HANDOFF_NOTE_ONLY = "HANDOFF_NOTE_ONLY"
    UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY = "UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY"
    # 不可逆边界
    FEE_OR_QUALIFICATION = "FEE_OR_QUALIFICATION"
    # 流程状态
    REVIEW_RETURNED = "REVIEW_RETURNED"
    PILOT_PAUSED = "PILOT_PAUSED"


SEVERITY: dict[str, str] = {
    Code.FIELD_UNMAPPED: "warn",
    Code.SOURCE_INCOMPLETE: "warn",
    Code.AUTHORIZATION_UNCLEAR: "error",
    Code.TIME_MISSING: "error",
    Code.TIME_UNCONFIRMED: "error",
    Code.TIME_INVALID: "error",
    Code.BACKFILLED_TIME: "warn",
    Code.NO_CURRENT_VERSION: "error",
    Code.VERSION_CONFLICT: "error",
    Code.VERSION_CHAIN_BREAK: "error",
    Code.VERSION_DUPLICATE: "warn",
    Code.VERSION_DRIFT: "error",
    Code.DUPLICATE_ID: "error",
    Code.UNIT_DIFFERENCE: "warn",
    Code.KEY_FIELD_MISSING: "error",
    Code.KEY_FIELD_UNCONFIRMED: "error",
    Code.HANDOFF_MISSING: "warn",
    Code.HANDOFF_NOTE_ONLY: "error",
    Code.UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY: "error",
    Code.FEE_OR_QUALIFICATION: "warn",
    Code.REVIEW_RETURNED: "warn",
    Code.PILOT_PAUSED: "warn",
}

TITLE: dict[str, str] = {
    Code.FIELD_UNMAPPED: "字段未映射",
    Code.SOURCE_INCOMPLETE: "来源不完整",
    Code.AUTHORIZATION_UNCLEAR: "授权范围不清",
    Code.TIME_MISSING: "时间字段缺失",
    Code.TIME_UNCONFIRMED: "时间未确认",
    Code.TIME_INVALID: "时间格式非法",
    Code.BACKFILLED_TIME: "回填时间晚于业务发生时间",
    Code.NO_CURRENT_VERSION: "无法定位当前版本",
    Code.VERSION_CONFLICT: "多个已确认版本同时有效",
    Code.VERSION_CHAIN_BREAK: "版本链断链",
    Code.VERSION_DUPLICATE: "同一版本重复登记",
    Code.VERSION_DRIFT: "交接版本与当前版本不一致",
    Code.DUPLICATE_ID: "工单编号重复",
    Code.UNIT_DIFFERENCE: "单位与口径差异",
    Code.KEY_FIELD_MISSING: "关键字段缺失",
    Code.KEY_FIELD_UNCONFIRMED: "关键字段未确认",
    Code.HANDOFF_MISSING: "缺少交接记录",
    Code.HANDOFF_NOTE_ONLY: "交接只有“已处理”备注",
    Code.UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY: "未确认项被归到对方名下",
    Code.FEE_OR_QUALIFICATION: "涉及收费或参展资格",
    Code.REVIEW_RETURNED: "人工复核退回",
    Code.PILOT_PAUSED: "试点已暂停",
}


def severity(code: str) -> str:
    return SEVERITY.get(code, "warn")


def title(code: str) -> str:
    return TITLE.get(code, code)
