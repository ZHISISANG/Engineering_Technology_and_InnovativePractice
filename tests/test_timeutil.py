"""工具函数测试：时间四态与单位换算直接决定口径是否可核对。"""

from __future__ import annotations

import unittest

from tests.helpers import ROOT, SRC  # noqa: F401  （同时完成 sys.path 设置）

from expo_trace.utils.timeutil import (
    gap_hours,
    iso,
    parse_duration_minutes,
    parse_time,
)


class ParseTimeTest(unittest.TestCase):
    def test_space_separated_local_time(self) -> None:
        value = parse_time("2026-10-12 09:10")
        self.assertTrue(value.ok)
        self.assertEqual(iso(value.value), "2026-10-12T09:10:00+08:00")

    def test_utc_is_converted_to_local(self) -> None:
        value = parse_time("2026-10-12T01:10:00Z")
        self.assertEqual(iso(value.value), "2026-10-12T09:10:00+08:00")

    def test_slash_and_chinese_date(self) -> None:
        self.assertEqual(iso(parse_time("2026/10/12").value), "2026-10-12T00:00:00+08:00")
        self.assertEqual(iso(parse_time("2026年10月12日").value), "2026-10-12T00:00:00+08:00")

    def test_missing_unconfirmed_invalid_are_distinct(self) -> None:
        self.assertEqual(parse_time("").state, "missing")
        self.assertEqual(parse_time("-").state, "missing")
        self.assertEqual(parse_time("未确认").state, "unconfirmed")
        self.assertEqual(parse_time("大概下周").state, "invalid")
        # 缺失和未确认不能合并成同一个状态（f31）
        self.assertNotEqual(parse_time("").state, parse_time("未确认").state)

    def test_custom_unconfirmed_tokens(self) -> None:
        value = parse_time("待法务确认", ("待法务确认",))
        self.assertEqual(value.state, "unconfirmed")
        self.assertIsNone(value.value)


class GapAndDurationTest(unittest.TestCase):
    def test_gap_hours_for_backfilled_ticket(self) -> None:
        occurred = parse_time("2026-10-14 09:00").value
        received = parse_time("2026-10-15 18:00").value
        self.assertEqual(gap_hours(occurred, received), 33.0)

    def test_gap_none_when_missing(self) -> None:
        self.assertIsNone(gap_hours(None, parse_time("2026-10-15 18:00").value))

    def test_hour_unit_is_converted_with_note(self) -> None:
        minutes, note = parse_duration_minutes(1.5, "hour")
        self.assertEqual(minutes, 90.0)
        self.assertIn("小时", note)

    def test_minute_unit_has_no_note(self) -> None:
        self.assertEqual(parse_duration_minutes(30, "分钟"), (30.0, ""))

    def test_missing_unit_is_flagged_not_silent(self) -> None:
        minutes, note = parse_duration_minutes(30, "")
        self.assertEqual(minutes, 30.0)
        self.assertIn("单位缺失", note)

    def test_unknown_unit_is_rejected(self) -> None:
        minutes, note = parse_duration_minutes(2, "周")
        self.assertIsNone(minutes)
        self.assertIn("无法识别", note)

    def test_non_numeric_value_is_rejected(self) -> None:
        minutes, note = parse_duration_minutes("半小时", "min")
        self.assertIsNone(minutes)
        self.assertIn("不是数字", note)


if __name__ == "__main__":
    unittest.main()
