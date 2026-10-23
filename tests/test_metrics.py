"""指标测试：三项并列、回填单列、抽查计划确定。"""

from __future__ import annotations

import unittest

from tests.helpers import FIXED_NOW, run_demo, temp_dir

from expo_trace.core.metrics import compute, sample_plan


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)
        cls.metrics = cls.result.metrics

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_three_metrics_are_all_present(self) -> None:
        for key in ("handling", "rework", "confirmation"):
            self.assertIn(key, self.metrics)
        self.assertIn("handling_minutes", self.metrics["formulas"])
        self.assertIn("rework_count", self.metrics["formulas"])
        self.assertIn("confirmation_completeness", self.metrics["formulas"])

    def test_handling_minutes_excludes_backfilled_from_clean_total(self) -> None:
        handling = self.metrics["handling"]
        self.assertEqual(handling["total_minutes"], 452)
        self.assertEqual(handling["backfilled_excluded"], 1)
        self.assertLess(handling["clean_total_minutes"], handling["total_minutes"])
        self.assertEqual(handling["coverage"], 1.0)

    def test_watermark_and_irreversible_counts(self) -> None:
        self.assertEqual(self.metrics["watermarked"], len(self.result.tickets))
        self.assertEqual(self.metrics["blocked_irreversible"], 2)

    def test_completeness_rate_starts_at_zero(self) -> None:
        confirmation = self.metrics["confirmation"]
        self.assertEqual(confirmation["confirmed_reviews"], 0)
        self.assertEqual(confirmation["rate"], 0.0)
        self.assertEqual(confirmation["required_items"], len(self.result.tickets) * 4)

    def test_sample_plan_is_deterministic(self) -> None:
        first = sample_plan(self.result.tickets, 80)
        second = sample_plan(self.result.tickets, 80)
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(first), 16)

    def test_metrics_recompute_is_stable(self) -> None:
        again = compute(
            self.result.tickets,
            self.result.resolutions,
            self.result.drafts,
            self.result.dispositions,
            self.result.queue,
            self.result.issues,
            reviews=[],
            config=self.result.config,
            store=self.result.store,
            paused=False,
            now=FIXED_NOW,
        )
        self.assertEqual(again["handling"], self.metrics["handling"])


if __name__ == "__main__":
    unittest.main()
