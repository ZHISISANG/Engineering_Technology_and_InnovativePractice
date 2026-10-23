"""YAML 子集解析：配置错了要在启动时明确报错，而不是悄悄用默认值。"""

from __future__ import annotations

import unittest

from tests.helpers import ROOT, SRC  # noqa: F401

from expo_trace.utils.errors import ConfigError
from expo_trace.utils.miniyaml import loads


class MiniYamlTest(unittest.TestCase):
    def test_nested_mapping_scalars_and_lists(self) -> None:
        data = loads(
            "actor: 张三\n"
            "pilot:\n"
            "  batch: 2026-S1\n"
            "  weeks: 4\n"
            "  read_only: true\n"
            "review:\n"
            "  reviewers: [顾明岚, 傅景]\n"
        )
        self.assertEqual(data["actor"], "张三")
        self.assertEqual(data["pilot"]["weeks"], 4)
        self.assertIs(data["pilot"]["read_only"], True)
        self.assertEqual(data["review"]["reviewers"], ["顾明岚", "傅景"])

    def test_block_list_of_scalars(self) -> None:
        data = loads(
            "routing:\n"
            "  blocking_codes:\n"
            "    - DUPLICATE_ID\n"
            "    - TIME_MISSING\n"
        )
        self.assertEqual(data["routing"]["blocking_codes"], ["DUPLICATE_ID", "TIME_MISSING"])

    def test_quotes_keep_hash_and_colon(self) -> None:
        data = loads('note: "a # b: c"\nplain: value # 注释会被丢掉\n')
        self.assertEqual(data["note"], "a # b: c")
        self.assertEqual(data["plain"], "value")

    def test_floats_and_null(self) -> None:
        data = loads("ratio: 1.5\nempty: null\n")
        self.assertEqual(data["ratio"], 1.5)
        self.assertIsNone(data["empty"])

    def test_duplicate_key_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            loads("a: 1\na: 2\n")

    def test_tab_indent_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            loads("a:\n\tb: 1\n")

    def test_list_of_mappings_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            loads("items:\n  - name: x\n    value: 1\n")

    def test_default_config_parses(self) -> None:
        from expo_trace.core.config import load_config

        config = load_config(ROOT / "config" / "default.yaml")
        self.assertEqual(config.get("pilot.batch"), "2026-S1")
        self.assertTrue(config.blocking_codes())


if __name__ == "__main__":
    unittest.main()
