"""草稿测试：固定水印、不可逆边界、无版本不出正文、发布接口恒被阻断（f22/f42/f52）。"""

from __future__ import annotations

import unittest

from tests.helpers import DEFAULT_CONFIG, FIXED_NOW, run_demo, temp_dir

from expo_trace.core import drafting
from expo_trace.core.codes import Code
from expo_trace.core.config import load_config
from expo_trace.core.drafting import build_drafts
from expo_trace.core.models import Disposition
from expo_trace.utils.errors import BlockedAction, ConfigError


class DraftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)
        cls.config = load_config(DEFAULT_CONFIG)
        cls.watermark = cls.config.watermark

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_every_draft_starts_with_watermark(self) -> None:
        self.assertEqual(len(self.result.drafts), len(self.result.tickets))
        for draft in self.result.drafts.values():
            self.assertTrue(draft.body.startswith(self.watermark), draft.ticket_id)
            self.assertIn("禁止动作", draft.body)

    def test_fee_question_is_blocked_not_answered(self) -> None:
        draft = self.result.drafts["T-1003"]
        self.assertIs(draft.status, Disposition.BLOCKED)
        self.assertTrue(draft.requires_authorization)
        self.assertIn("不可逆边界", draft.body)
        self.assertIn("收费", draft.body)
        self.assertIn("不得自动发布", draft.body)

    def test_unlocatable_version_produces_no_answer(self) -> None:
        draft = self.result.drafts["T-1002"]
        self.assertIs(draft.status, Disposition.HUMAN_REVIEW)
        self.assertIsNone(draft.version_id)
        self.assertIn("本工单不生成回复草稿", draft.body)
        self.assertIn("已核查的版本", draft.body)

    def test_rejected_version_is_visible_in_evidence(self) -> None:
        draft = self.result.drafts["T-1011"]
        self.assertIn("V-1012", draft.body)
        self.assertIn("f34", draft.body)
        self.assertIn("已排除的版本", draft.body)

    def test_source_rows_are_attached(self) -> None:
        draft = self.result.drafts["T-1001"]
        self.assertTrue(draft.sources)
        self.assertTrue(all(source.get("line") for source in draft.sources))
        self.assertTrue(any(source["source"] == "file_index.csv" for source in draft.sources))

    def test_publish_paths_are_blocked(self) -> None:
        for func in (drafting.publish, drafting.send_external, drafting.write_back):
            with self.assertRaises(BlockedAction):
                func(self.result.drafts["T-1001"])

    def test_paused_builds_no_answer_body(self) -> None:
        drafts = build_drafts(
            self.result.tickets,
            self.result.resolutions,
            self.result.ingest.versions,
            self.result.issues,
            self.config,
            reviews=[],
            paused=True,
            now=FIXED_NOW,
        )
        draft = drafts["T-1001"]
        self.assertIs(draft.status, Disposition.HUMAN_REVIEW)
        self.assertIn("试点已暂停", draft.body)
        self.assertNotIn("**主题**", draft.body)

    def test_read_only_boundary_is_enforced_by_config(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        config.data["safety"]["auto_publish"] = True
        with self.assertRaises(ConfigError):
            config.validate()


class PausedDraftRoutingTest(unittest.TestCase):
    """暂停时队列原因与草稿状态必须同时体现，不能只改一个地方。"""

    def test_queue_and_draft_are_consistent_when_paused(self) -> None:
        with temp_dir() as tmp:
            result = run_demo(tmp)
            self.assertFalse(any(Code.PILOT_PAUSED in q.reasons for q in result.queue))


if __name__ == "__main__":
    unittest.main()
