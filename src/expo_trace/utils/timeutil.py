"""时间口径工具：

- 业务发生时间（business occurred）与系统接收时间（system received）必须分开记录；
  现场“先群消息、后补填”导致回填时间不等于发生时间（证据 f13）。
- 所有时间统一转到 +08:00（Asia/Shanghai，无夏令时）后比较与输出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from expo_trace.utils.errors import TraceError

LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_UNCONFIRMED = ("未确认", "待确认", "待核实", "unknown", "tbd", "n/a", "na", "?")
_MISSING_TOKENS = {"", "-", "—", "--", "null", "none", "nan"}


@dataclass(frozen=True)
class TimeValue:
    """带状态的解析结果：缺失、未确认、非法与正常值必须可区分。"""

    value: datetime | None
    state: str  # ok / missing / unconfirmed / invalid
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok" and self.value is not None

    @property
    def decidable(self) -> bool:
        """只有正常值可用于时间判定。"""
        return self.ok


def _token_set(extra: tuple[str, ...]) -> set[str]:
    return {t.strip().lower() for t in (*DEFAULT_UNCONFIRMED, *extra) if t.strip()}


def parse_time(raw: object, unconfirmed_tokens: tuple[str, ...] = ()) -> TimeValue:
    """解析时间字段并标注状态。

    支持：ISO8601（含 Z / 偏移）、``2026-10-12 09:10``、``2026/10/12``、纯日期。
    无时区信息时按 +08:00 解释，并在输出中显式带偏移。
    """
    if raw is None:
        return TimeValue(None, "missing", "")
    text = str(raw).strip()
    if text.lower() in _MISSING_TOKENS:
        return TimeValue(None, "missing", text)
    if text.lower() in _token_set(unconfirmed_tokens):
        return TimeValue(None, "unconfirmed", text)

    candidate = text.replace("年", "-").replace("月", "-").replace("日", "").strip()
    candidate = candidate.replace("/", "-")
    if candidate.endswith("Z") or candidate.endswith("z"):
        candidate = candidate[:-1] + "+00:00"
    # 允许 "2026-10-12 09:10" 这种空格分隔
    candidate = re.sub(r"\s+", "T", candidate, count=1) if " " in candidate else candidate

    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return TimeValue(None, "invalid", text)
    return TimeValue(to_local(parsed), "ok", text)


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else to_local(dt).isoformat(timespec="seconds")


def gap_hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((to_local(end) - to_local(start)).total_seconds() / 3600.0, 2)


def parse_duration_minutes(value: object, unit: object) -> tuple[float | None, str]:
    """把“处理时长 + 单位”归一为分钟。

    返回 ``(分钟数, 说明)``。说明为空表示无需提示；否则是单位缺失/不一致的原因。
    单位差异不能静默吞掉（f33），必须留下可读说明。
    """
    if value is None or str(value).strip() == "":
        return None, "缺少处理时长"
    text = str(value).strip().replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None, f"处理时长不是数字：{value!r}"
    unit_text = "" if unit is None else str(unit).strip().lower()
    if unit_text in {"", "none"}:
        return round(number, 2), "单位缺失，按分钟记录"
    if unit_text in {"min", "mins", "minute", "minutes", "分钟", "分"}:
        return round(number, 2), ""
    if unit_text in {"h", "hr", "hrs", "hour", "hours", "小时", "时"}:
        return round(number * 60.0, 2), f"单位由小时换算为分钟（{value}{unit}）"
    if unit_text in {"s", "sec", "second", "seconds", "秒"}:
        return round(number / 60.0, 2), f"单位由秒换算为分钟（{value}{unit}）"
    return None, f"无法识别的单位：{unit!r}"


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise TraceError("内部时间必须带时区")
    return dt
