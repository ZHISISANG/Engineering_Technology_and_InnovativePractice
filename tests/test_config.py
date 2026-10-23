"""配置测试：只读边界、必填项、责任人覆盖。"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import DEFAULT_CONFIG, temp_dir

from expo_trace.core.config import load_config
from expo_trace.utils.errors import ConfigError


class ConfigTest(unittest.TestCase):
    def test_default_config_is_valid(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual(config.get("pilot.batch"), "2026-S1")
        self.assertTrue(config.watermark)
        self.assertGreaterEqual(len(config.blocking_codes()), 10)
        self.assertTrue(config.reviewers())

    def test_every_blocking_code_has_an_owner(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        owners = config.owners()
        for code in config.blocking_codes():
            self.assertIn(code, owners, code)
            self.assertTrue(owners[code])

    def test_read_only_cannot_be_turned_off(self) -> None:
        text = Path(DEFAULT_CONFIG).read_text(encoding="utf-8")
        broken = text.replace("  read_only: true", "  read_only: false")
        with temp_dir() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(broken, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_auto_publish_cannot_be_enabled(self) -> None:
        text = Path(DEFAULT_CONFIG).read_text(encoding="utf-8")
        broken = text.replace("  auto_publish: false", "  auto_publish: true")
        with temp_dir() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(broken, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_blocking_codes_are_required(self) -> None:
        text = Path(DEFAULT_CONFIG).read_text(encoding="utf-8")
        broken = text.replace("  blocking_codes:", "  blocking_codes_disabled:")
        with temp_dir() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(broken, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_unknown_mapping_entity_is_empty(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual(config.mapping("nope"), {})


if __name__ == "__main__":
    unittest.main()
