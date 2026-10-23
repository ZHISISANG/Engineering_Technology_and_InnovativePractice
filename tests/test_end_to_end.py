"""端到端测试：产物完整性、可复现、人工确认进入指标。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.helpers import FIXED_NOW, run_demo, temp_dir

from expo_trace.core.review import submit_review
from expo_trace.core.store import RunStore


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)
        cls.out = Path(cls._tmp.name) / "output"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_output_inventory(self) -> None:
        expected = {
            "tickets.jsonl",
            "versions.jsonl",
            "resolutions.json",
            "issues.jsonl",
            "issues.csv",
            "drafts.jsonl",
            "queue.jsonl",
            "queue.csv",
            "checklist.md",
            "report.md",
            "metrics.json",
            "acceptance.json",
        }
        present = {path.name for path in self.out.iterdir() if path.is_file()}
        self.assertTrue(expected.issubset(present), expected - present)
        self.assertEqual(len(list((self.out / "drafts").glob("*.md"))), 16)

    def test_report_has_all_sections(self) -> None:
        report = (self.out / "report.md").read_text(encoding="utf-8")
        for heading in ("结论摘要", "三项指标", "人工队列", "问题分布", "验收矩阵", "抽查计划", "限制"):
            self.assertIn(heading, report)
        self.assertIn("只用工时判断效果会掩盖返工", report)
        self.assertIn("未确认 · 不可对外", report)

    def test_checklist_lists_missing_items(self) -> None:
        checklist = (self.out / "checklist.md").read_text(encoding="utf-8")
        self.assertIn("T-1001", checklist)
        self.assertIn("缺项", checklist)
        self.assertIn("[ ]", checklist)

    def test_queue_csv_matches_queue(self) -> None:
        lines = (self.out / "queue.csv").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), len(self.result.queue) + 1)

    def test_second_run_is_reproducible(self) -> None:
        with temp_dir() as other:
            again = run_demo(other)
            for name in ("tickets.jsonl", "drafts.jsonl", "issues.jsonl", "resolutions.json"):
                self.assertEqual(
                    (self.out / name).read_text(encoding="utf-8"),
                    (Path(other) / "output" / name).read_text(encoding="utf-8"),
                    name,
                )
            self.assertEqual(self.result.dispositions, again.dispositions)

    def test_audit_records_each_version_decision(self) -> None:
        entries = self.result.store.read_jsonl("audit.jsonl")
        resolved = {
            entry["subject"]: entry["details"]
            for entry in entries
            if entry.get("action") == "version_resolved"
        }
        self.assertEqual(len(resolved), 16)
        self.assertEqual(resolved["T-1011"]["version_id"], "V-1002")
        self.assertEqual(resolved["T-1011"]["state"], "current")
        self.assertEqual(resolved["T-1002"]["state"], "expired")
        self.assertEqual(resolved["T-1002"]["reason_code"], "NO_CURRENT_VERSION")

    def test_review_updates_completeness_when_rerun(self) -> None:
        with temp_dir() as tmp:
            first = run_demo(tmp)
            required = [str(x) for x in first.config.get("review.required_confirmations", [])]
            submit_review(
                first.store,
                first.config,
                ticket_id="T-1001",
                reviewer="傅景",
                decision="confirm",
                note="",
                confirmed_items=required,
                draft=first.drafts["T-1001"],
                now=FIXED_NOW,
            )
            second = run_demo(tmp)
            confirmation = second.metrics["confirmation"]
            self.assertEqual(confirmation["confirmed_reviews"], 1)
            self.assertEqual(confirmation["covered_items"], 4)
            self.assertGreater(confirmation["rate"], 0.0)
            self.assertTrue(second.store.read_jsonl("audit.jsonl"))


if __name__ == "__main__":
    unittest.main()
