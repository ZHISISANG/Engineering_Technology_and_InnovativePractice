"""Web 配置（Settings.from_env）的回归测试：显式指定的运行目录不得静默回落。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from expo_trace.utils.errors import InputError
from expo_trace.web.settings import ROOT, Settings
from tests.helpers import temp_dir


class SettingsEnvTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k)
                       for k in ("EXPO_RUN_DIR", "EXPO_WEB_DB", "EXPO_WEB_SECRET")}

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_explicit_unusable_run_dir_raises(self) -> None:
        with temp_dir() as tmp:
            empty = Path(tmp) / "empty-run"
            empty.mkdir()
            os.environ["EXPO_RUN_DIR"] = str(empty)
            with self.assertRaises(InputError) as ctx:
                Settings.from_env()
            self.assertIn("tickets.jsonl", str(ctx.exception))
            self.assertIn("empty-run", str(ctx.exception))

    def test_explicit_usable_run_dir_is_kept(self) -> None:
        final = ROOT / "runs" / "final"
        if not (final / "output" / "tickets.jsonl").exists():
            self.skipTest("缺少 runs/final 演示产物")
        os.environ["EXPO_RUN_DIR"] = str(final)
        self.assertEqual(Settings.from_env().run_dir, final.resolve())

    def test_default_falls_back_to_demo_when_final_missing(self) -> None:
        os.environ.pop("EXPO_RUN_DIR", None)
        settings = Settings.from_env()
        self.assertTrue((settings.run_dir / "output" / "tickets.jsonl").exists(),
                        f"回落目录不可用：{settings.run_dir}")

    def test_db_path_comes_from_env(self) -> None:
        with temp_dir() as tmp:
            db = Path(tmp) / "web" / "app.db"
            os.environ["EXPO_WEB_DB"] = str(db)
            self.assertEqual(Settings.from_env().db_path, db.resolve())

    def test_default_secret_is_flagged(self) -> None:
        os.environ.pop("EXPO_WEB_SECRET", None)
        self.assertTrue(Settings.from_env().is_default_secret)


if __name__ == "__main__":
    unittest.main()
