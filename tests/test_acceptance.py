"""验收矩阵测试：工具侧覆盖 vs 业务验收未执行，两者不能混为一谈。"""

from __future__ import annotations

import unittest

from tests.helpers import run_demo, temp_dir

from expo_trace.core.acceptance import evaluate


class AcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_six_requirements_have_tool_side_evidence(self) -> None:
        rows = {row["req"]: row for row in self.result.acceptance["rows"]}
        for req in ("REQ-1", "REQ-2", "REQ-3", "REQ-4", "REQ-5", "REQ-6"):
            self.assertIn(req, rows)
            self.assertEqual(rows[req]["status"], "PASS", rows[req])
            self.assertTrue(rows[req]["evidence"])

    def test_business_acceptance_is_not_run(self) -> None:
        rows = {row["req"]: row for row in self.result.acceptance["rows"]}
        business = rows["业务验收"]
        self.assertEqual(business["status"], "NOT_RUN")
        self.assertIn("80", business["title"])

    def test_publish_channel_is_part_of_req6(self) -> None:
        result = evaluate(
            self.result.config,
            self.result.ingest,
            self.result.tickets,
            self.result.resolutions,
            self.result.drafts,
            self.result.queue,
            self.result.dispositions,
            self.result.issues,
            self.result.metrics,
            publish_blocked=False,
        )
        rows = {row["req"]: row for row in result["rows"]}
        self.assertEqual(rows["REQ-6"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
