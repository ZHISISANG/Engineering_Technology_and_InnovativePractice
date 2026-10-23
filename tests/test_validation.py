"""数据核查测试：四类脏数据、缺失与未确认的区分、兜底项进入队列（f23/f31/f33/f44）。"""

from __future__ import annotations

import unittest

from tests.helpers import DEFAULT_CONFIG, FIXED_NOW, run_demo, temp_dir, ticket

from expo_trace.core.codes import Code
from expo_trace.core.config import load_config
from expo_trace.core.models import IngestResult
from expo_trace.core.validation import blocking_issues, run_checks
from expo_trace.core.versioning import resolve_all


class DemoChecksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)
        cls.codes = {issue.code for issue in cls.result.issues}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_four_dirty_data_classes_are_flagged(self) -> None:
        for code in (
            Code.DUPLICATE_ID,
            Code.TIME_MISSING,
            Code.VERSION_CHAIN_BREAK,
            Code.UNIT_DIFFERENCE,
        ):
            self.assertIn(code, self.codes, code)

    def test_expected_fallback_triggers(self) -> None:
        for code in (
            Code.NO_CURRENT_VERSION,
            Code.VERSION_CONFLICT,
            Code.AUTHORIZATION_UNCLEAR,
            Code.KEY_FIELD_UNCONFIRMED,
            Code.HANDOFF_NOTE_ONLY,
            Code.UNCONFIRMED_ASSIGNED_TO_COUNTERPARTY,
            Code.VERSION_DRIFT,
        ):
            self.assertIn(code, self.codes, code)

    def test_issue_keeps_original_row(self) -> None:
        must_have_raw = {
            Code.DUPLICATE_ID,
            Code.UNIT_DIFFERENCE,
            Code.TIME_MISSING,
            Code.BACKFILLED_TIME,
            Code.HANDOFF_NOTE_ONLY,
        }
        for issue in self.result.issues:
            if issue.code in must_have_raw:
                self.assertTrue(issue.raw, f"{issue.code} 缺少原行")

    def test_blocking_issues_cover_queue_tickets(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        grouped = blocking_issues(self.result.issues, config.blocking_codes())
        queued = {item.ticket_id for item in self.result.queue}
        for ticket_id in ("T-1004", "T-1005", "T-1008", "T-1009"):
            self.assertIn(ticket_id, grouped)
            self.assertIn(ticket_id, queued)


class MissingVersusUnconfirmedTest(unittest.TestCase):
    def test_missing_and_unconfirmed_are_not_merged(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        missing = ticket("T-MISSING", occurred="", received="")
        unconfirmed = ticket("T-UNCONFIRMED", occurred="未确认", received="2026-10-12 09:05")
        ingest = IngestResult(tickets=[missing, unconfirmed])
        resolutions = resolve_all(ingest.tickets, [], tuple(config.get("quality.unconfirmed_tokens", [])))
        issues = run_checks(ingest, config, resolutions, FIXED_NOW)
        by_ticket = {}
        for issue in issues:
            by_ticket.setdefault(issue.ticket_id, set()).add(issue.code)
        self.assertIn(Code.TIME_MISSING, by_ticket["T-MISSING"])
        self.assertIn(Code.TIME_UNCONFIRMED, by_ticket["T-UNCONFIRMED"])
        self.assertEqual(missing.occurred_state.value, "missing")
        self.assertEqual(unconfirmed.occurred_state.value, "unconfirmed")


if __name__ == "__main__":
    unittest.main()
