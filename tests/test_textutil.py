"""文本与脱敏工具测试：导出必须脱敏，但保留可核对的结构。"""

from __future__ import annotations

import unittest

from tests.helpers import ROOT, SRC  # noqa: F401

from expo_trace.utils.textutil import (
    contains_any,
    mask_pii,
    normalize_key,
    sha256_bytes,
    truncate,
)


class MaskPiiTest(unittest.TestCase):
    def test_phone_email_and_id_are_masked(self) -> None:
        raw = "联系人 13800001111，邮箱 zhang.san@example.com，证件 11010119900101123X"
        masked = mask_pii(raw)
        self.assertNotIn("13800001111", masked)
        self.assertNotIn("zhang.san@example.com", masked)
        self.assertNotIn("11010119900101123X", masked)
        self.assertIn("138****1111", masked)

    def test_mask_is_idempotent(self) -> None:
        once = mask_pii("电话 13800001111")
        self.assertEqual(mask_pii(once), once)

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(mask_pii("展位在 1 号馆"), "展位在 1 号馆")


class TextHelpersTest(unittest.TestCase):
    def test_contains_any_returns_matched_keywords(self) -> None:
        self.assertEqual(contains_any("展位费能否退款", ["收费", "退款", "资格"]), ["退款"])

    def test_normalize_key_handles_full_width(self) -> None:
        self.assertEqual(normalize_key(" 展位费 "), "展位费")
        self.assertEqual(normalize_key("ＡＢＣ"), "abc")

    def test_sha256_stable(self) -> None:
        self.assertEqual(sha256_bytes(b"a"), sha256_bytes(b"a"))
        self.assertNotEqual(sha256_bytes(b"a"), sha256_bytes(b"b"))

    def test_truncate_marks_ellipsis(self) -> None:
        self.assertEqual(truncate("abcdef", 4), "abc…")
        self.assertLessEqual(len(truncate("abcdef", 4)), 4)
        self.assertEqual(truncate("abc", 4), "abc")


if __name__ == "__main__":
    unittest.main()
