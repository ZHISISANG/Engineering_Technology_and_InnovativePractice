"""版本判定测试：有效期与确认状态优先，文件名与接收时间不能替代规则（f10/f11/f24/f34）。"""

from __future__ import annotations

import unittest

from tests.helpers import ticket, version

from expo_trace.core.codes import Code
from expo_trace.core.models import VersionState
from expo_trace.core.versioning import duplicate_versions, resolve_ticket
from expo_trace.utils.timeutil import parse_time


class ResolveVersionTest(unittest.TestCase):
    def test_filename_never_decides(self) -> None:
        versions = [
            version("V-2026-07", start="2026-07-01", confirmed=True, confirmed_by="唐映秋"),
            version(
                "V-2026-01",
                start="2026-01-01",
                end="2026-06-30",
                confirmed=False,
                file_name="最新版_展位说明_最终版.pdf",
            ),
        ]
        resolution = resolve_ticket(ticket(), versions)
        self.assertIs(resolution.state, VersionState.CURRENT)
        self.assertEqual(resolution.version_id, "V-2026-07")
        rejected = {c["version_id"]: c for c in resolution.considered}["V-2026-01"]
        self.assertIn("不参与判定", rejected["why"])

    def test_later_unconfirmed_cannot_override_confirmed(self) -> None:
        versions = [
            version("V-CONFIRMED", start="2026-07-01", confirmed=True, received_at=parse_time("2026-07-02").value),
            version(
                "V-LATE",
                start="2026-07-01",
                confirmed=False,
                received_at=parse_time("2026-10-18").value,
                file_name="最新版_最终版.pdf",
            ),
        ]
        resolution = resolve_ticket(ticket(), versions)
        self.assertEqual(resolution.version_id, "V-CONFIRMED")
        late = {c["version_id"]: c for c in resolution.considered}["V-LATE"]
        self.assertIn("f34", late["why"])

    def test_expired_is_not_current(self) -> None:
        versions = [version("V-OLD", start="2026-01-01", end="2026-06-30", confirmed=True)]
        resolution = resolve_ticket(ticket(occurred="2026-10-12 09:00"), versions)
        self.assertIs(resolution.state, VersionState.EXPIRED)
        self.assertIsNone(resolution.version_id)
        self.assertEqual(resolution.reason_code, Code.NO_CURRENT_VERSION)
        self.assertIn("V-OLD", resolution.reason)

    def test_void_version_keeps_reason(self) -> None:
        versions = [
            version(
                "V-VOID",
                start="2026-07-01",
                end="2026-12-31",
                confirmed=True,
                void=True,
                void_reason="展位图调整，原说明作废",
            )
        ]
        resolution = resolve_ticket(ticket(), versions)
        self.assertIs(resolution.state, VersionState.EXPIRED)
        self.assertIn("展位图调整，原说明作废", resolution.reason)

    def test_two_confirmed_versions_conflict(self) -> None:
        versions = [
            version("V-A", start="2026-07-01", confirmed=True, confirmed_by="唐映秋"),
            version("V-B", start="2026-09-01", confirmed=True, confirmed_by="傅景"),
        ]
        resolution = resolve_ticket(ticket(), versions)
        self.assertIs(resolution.state, VersionState.UNCONFIRMED)
        self.assertEqual(resolution.reason_code, Code.VERSION_CONFLICT)
        self.assertIsNone(resolution.version_id)

    def test_supersede_chain_picks_successor(self) -> None:
        versions = [
            version("V-OLD", start="2026-07-01", confirmed=True),
            version("V-NEW", start="2026-09-01", confirmed=True, supersedes="V-OLD"),
        ]
        resolution = resolve_ticket(ticket(occurred="2026-10-12 09:00"), versions)
        self.assertIs(resolution.state, VersionState.CURRENT)
        self.assertEqual(resolution.version_id, "V-NEW")

    def test_broken_chain_is_not_default_pass(self) -> None:
        versions = [
            version("V-BROKEN", start="2026-07-01", confirmed=True, supersedes="V-MISSING"),
        ]
        resolution = resolve_ticket(ticket(), versions)
        self.assertIs(resolution.state, VersionState.UNCONFIRMED)
        self.assertEqual(resolution.reason_code, Code.VERSION_CHAIN_BREAK)
        self.assertTrue(resolution.chain_break)

    def test_no_timestamp_cannot_be_decided(self) -> None:
        resolution = resolve_ticket(ticket(occurred="", received=""), [version("V-1", confirmed=True)])
        self.assertIs(resolution.state, VersionState.UNCONFIRMED)
        self.assertEqual(resolution.reason_code, Code.KEY_FIELD_MISSING)
        self.assertFalse(resolution.locatable)

    def test_only_unconfirmed_candidates(self) -> None:
        versions = [version("V-U", start="2026-07-01", confirmed=False)]
        resolution = resolve_ticket(ticket(), versions)
        self.assertIs(resolution.state, VersionState.UNCONFIRMED)
        self.assertEqual(resolution.reason_code, Code.KEY_FIELD_UNCONFIRMED)
        self.assertIn("未确认", resolution.reason)

    def test_duplicate_version_records_are_reported(self) -> None:
        versions = [
            version("V-DUP", confirmed=True, raw={"version_id": "V-DUP", "row": 2}),
            version("V-DUP", confirmed=True, confirmed_by="傅景", raw={"version_id": "V-DUP", "row": 9}),
        ]
        issues = duplicate_versions(versions)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, Code.VERSION_DUPLICATE)
        self.assertTrue(issues[0].raw)


if __name__ == "__main__":
    unittest.main()
