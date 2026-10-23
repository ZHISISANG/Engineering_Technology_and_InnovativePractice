"""人工确认测试：确认项必须齐全、权限与不可逆边界、退回原因、重复提交。"""

from __future__ import annotations

import unittest

from tests.helpers import FIXED_NOW, run_demo, temp_dir

from expo_trace.core.review import submit_review
from expo_trace.utils.errors import ReviewRejected


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = temp_dir()
        self.result = run_demo(self._tmp.name)
        self.store = self.result.store
        self.config = self.result.config
        self.required = [str(x) for x in self.config.get("review.required_confirmations", [])]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _submit(self, ticket_id: str, reviewer: str, decision: str = "confirm", note: str = "", items=None):
        return submit_review(
            self.store,
            self.config,
            ticket_id=ticket_id,
            reviewer=reviewer,
            decision=decision,
            note=note,
            confirmed_items=self.required if items is None else items,
            draft=self.result.drafts[ticket_id],
            now=FIXED_NOW,
        )

    def test_confirm_writes_review_and_audit(self) -> None:
        payload = self._submit("T-1001", "傅景")
        self.assertEqual(payload["decision"], "confirm")
        self.assertEqual(len(self.store.reviews()), 1)
        audit = [row for row in self.result.store.read_jsonl("audit.jsonl") if row["action"] == "review_submitted"]
        self.assertEqual(audit[-1]["actor"], "傅景")

    def test_incomplete_confirmation_is_rejected(self) -> None:
        with self.assertRaises(ReviewRejected) as ctx:
            self._submit("T-1001", "傅景", items=self.required[:1])
        self.assertIn("确认项不完整", str(ctx.exception))

    def test_unlisted_reviewer_is_rejected(self) -> None:
        with self.assertRaises(ReviewRejected):
            self._submit("T-1001", "林亦舟")

    def test_fee_draft_requires_authorized_reviewer(self) -> None:
        with self.assertRaises(ReviewRejected) as ctx:
            self._submit("T-1003", "叶书")
        self.assertIn("无权确认", str(ctx.exception))
        payload = self._submit("T-1003", "顾明岚")
        self.assertEqual(payload["ticket_id"], "T-1003")

    def test_return_needs_reason(self) -> None:
        with self.assertRaises(ReviewRejected):
            self._submit("T-1001", "傅景", decision="return", note="不行")
        payload = self._submit("T-1001", "傅景", decision="return", note="版本口径与最新通知不一致，需重查")
        self.assertEqual(payload["decision"], "return")

    def test_duplicate_submission_is_rejected(self) -> None:
        self._submit("T-1001", "傅景")
        with self.assertRaises(ReviewRejected):
            self._submit("T-1001", "傅景")

    def test_cannot_confirm_unlocatable_version(self) -> None:
        with self.assertRaises(ReviewRejected):
            self._submit("T-1002", "傅景")


if __name__ == "__main__":
    unittest.main()
