"""CLI 冒烟测试：退出码与中文输出，覆盖运行/暂停/确认/审计。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import ROOT, SRC, temp_dir

from expo_trace.core.config import load_config


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    """跑一次 CLI，把 stdout/stderr 重定向到文件再读回。

    不用 subprocess 的管道：部分受限环境（Windows 沙箱）禁止创建匿名管道，
    重定向到文件既避开该限制，也避免编码与截断问题。
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    holder = temp_dir()
    out_path = Path(holder.name) / "stdout.txt"
    err_path = Path(holder.name) / "stderr.txt"
    try:
        with out_path.open("w", encoding="utf-8") as out, err_path.open("w", encoding="utf-8") as err:
            proc = subprocess.run(
                [sys.executable, "-m", "expo_trace", *args],
                cwd=ROOT,
                env=env,
                stdout=out,
                stderr=err,
                timeout=120,
            )
        stdout = out_path.read_text(encoding="utf-8", errors="replace")
        stderr = err_path.read_text(encoding="utf-8", errors="replace")
    finally:
        holder.cleanup()
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = temp_dir()
        self.run_dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return cli(
            "run",
            "--export",
            "data/demo/export",
            "--run-dir",
            self.run_dir,
            "--now",
            "2026-10-20 10:00",
            *extra,
        )

    def test_run_prints_summary(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("工单：16", proc.stdout)
        self.assertIn("可出草稿：5", proc.stdout)
        self.assertIn("人工队列：9", proc.stdout)
        self.assertTrue((Path(self.run_dir) / "output" / "report.md").exists())

    def test_run_json_mode(self) -> None:
        proc = self._run("--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["manifest"]["counts"]["tickets"], 16)

    def test_pause_requires_authorized_role_and_blocks_run(self) -> None:
        self.assertEqual(self._run().returncode, 0)
        denied = cli("pause", "--run-dir", self.run_dir, "--actor", "林亦舟", "--reason", "测试")
        self.assertEqual(denied.returncode, 4)
        allowed = cli("pause", "--run-dir", self.run_dir, "--actor", "傅景", "--reason", "测试暂停")
        self.assertEqual(allowed.returncode, 0)
        blocked = self._run()
        self.assertEqual(blocked.returncode, 3)
        resumed = cli("resume", "--run-dir", self.run_dir, "--actor", "傅景")
        self.assertEqual(resumed.returncode, 0)

    def test_review_requires_all_items(self) -> None:
        self.assertEqual(self._run().returncode, 0)
        config = load_config(ROOT / "config" / "default.yaml")
        required = [str(x) for x in config.get("review.required_confirmations", [])]
        partial = cli(
            "review", "--run-dir", self.run_dir, "--ticket", "T-1001",
            "--reviewer", "傅景", "--decision", "confirm", "--item", required[0],
        )
        self.assertEqual(partial.returncode, 4)
        self.assertIn("确认项不完整", partial.stderr + partial.stdout)

        args = ["review", "--run-dir", self.run_dir, "--ticket", "T-1001", "--reviewer", "傅景", "--decision", "confirm"]
        for item in required:
            args.extend(["--item", item])
        complete = cli(*args)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        rows = (Path(self.run_dir) / "reviews.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)

    def test_audit_verify_and_sample(self) -> None:
        self.assertEqual(self._run().returncode, 0)
        verified = cli("audit", "verify", "--run-dir", self.run_dir)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("审计链完整", verified.stdout)
        sample = cli("sample", "--run-dir", self.run_dir, "--size", "5", "--json")
        self.assertEqual(sample.returncode, 0, sample.stderr)
        payload = json.loads(sample.stdout)
        self.assertEqual(payload["size"], 5)
        self.assertEqual(len(payload["tickets"]), 5)

        shown = cli("audit", "show", "--run-dir", self.run_dir, "--ticket", "T-1011")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("version_resolved", shown.stdout)
        self.assertIn("V-1002", shown.stdout)
        # 过滤只按对象匹配，不把包含该编号的汇总事件混进来
        self.assertNotIn("run_completed", shown.stdout)


if __name__ == "__main__":
    unittest.main()
