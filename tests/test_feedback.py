"""误报 / 漏报 / 未确认登记（f55 / REQ-5）。"""

from __future__ import annotations

import unittest

from tests.helpers import FIXED_NOW, run_demo, temp_dir

from expo_trace.core.audit import AuditLog
from expo_trace.core.config import load_config
from expo_trace.core.feedback import submit_feedback
from expo_trace.core.store import RunStore
from expo_trace.utils.errors import ReviewRejected


class FeedbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = temp_dir()
        self.result = run_demo(self._tmp.name)
        self.store = RunStore(self._tmp.name)
        self.config = load_config(self.result.config.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_submit_writes_feedback_and_audit(self) -> None:
        record = submit_feedback(
            self.store,
            self.config,
            ticket_id="T-1005",
            kind="误报",
            reporter="傅景",
            note="T-1005 的冲突已由业务确认，工具判为冲突属于误报",
            now=FIXED_NOW,
        )
        self.assertEqual(record["kind"], "false_positive")
        self.assertEqual(record["kind_label"], "误报")
        rows = self.store.read_jsonl("feedback.jsonl")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticket_id"], "T-1005")
        entries = AuditLog(self.store, actor="傅景").read()
        self.assertEqual(entries[-1]["action"], "feedback_submitted")
        self.assertEqual(entries[-1]["actor"], "傅景")

    def test_note_and_kind_validation(self) -> None:
        with self.assertRaises(ReviewRejected):
            submit_feedback(
                self.store, self.config, ticket_id="T-1005", kind="误报", reporter="傅景", note="短"
            )
        with self.assertRaises(ReviewRejected):
            submit_feedback(
                self.store,
                self.config,
                ticket_id="T-1005",
                kind="不相干",
                reporter="傅景",
                note="这说明类型不受支持",
            )

    def test_metrics_and_report_include_feedback(self) -> None:
        submit_feedback(
            self.store,
            self.config,
            ticket_id="T-1010",
            kind="未确认",
            reporter="叶书",
            note="单位换算说明需要一线再确认",
            now=FIXED_NOW,
        )
        result = run_demo(self._tmp.name)
        self.assertEqual(result.metrics["feedback"]["total"], 1)
        self.assertEqual(result.metrics["feedback"]["by_kind"], {"未确认": 1})
        report = result.report_path.read_text(encoding="utf-8")
        self.assertIn("误报 / 漏报 / 未确认（本周登记）", report)
        self.assertIn("T-1010", report)
        self.assertIn("单位换算说明需要一线再确认", report)


if __name__ == "__main__":
    unittest.main()
