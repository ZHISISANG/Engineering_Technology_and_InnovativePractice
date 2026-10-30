"""回归测试：确认完整率去重、小数工时、坏输入不留残目录、CLI 边界退出码。

这些用例对应实际排查中发现的问题，防止同类缺陷回潮：
- 同一工单被多人确认时，必确认项被重复计数，完整率可被推到 100% 以上；
- 台账里的 25.7 分钟被 int() 截成 25；
- 导出目录缺文件时先建了运行目录，留下空壳；
- run 目录被同名文件占住时抛出未捕获的 OSError（用户看到栈）；
- ``sample --size 0 / -5`` 静默成功；暂停时 ``report`` 仍返回 0。
"""

from __future__ import annotations

import contextlib
import io
import unittest
from dataclasses import replace
from pathlib import Path

from tests.helpers import DEFAULT_CONFIG, DEMO_EXPORT, FIXED_NOW, run_demo, temp_dir

from expo_trace.core import metrics as metrics_mod
from expo_trace.core.pipeline import execute
from expo_trace.main import main as cli_main
from expo_trace.utils.errors import InputError


def call_cli(*args: str) -> tuple[int, str, str]:
    """进程内跑一次 CLI，避免沙箱对子进程管道的限制。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(list(args))
    return code, out.getvalue(), err.getvalue()


class MetricsRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)
        cls.required = [str(x) for x in cls.result.config.get("review.required_confirmations", [])]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _metrics(self, reviews: list[dict], tickets=None) -> dict:
        result = self.result
        return metrics_mod.compute(
            tickets if tickets is not None else result.tickets,
            result.resolutions,
            result.drafts,
            result.dispositions,
            result.queue,
            result.issues,
            reviews=reviews,
            config=result.config,
            store=result.store,
            paused=False,
            now=FIXED_NOW,
        )

    def test_duplicate_confirmation_counts_items_once(self) -> None:
        reviews = [
            {
                "ticket_id": "T-1001",
                "reviewer": reviewer,
                "decision": "confirm",
                "confirmed_items": list(self.required),
            }
            for reviewer in ("傅景", "顾明岚")
        ]
        confirmation = self._metrics(reviews)["confirmation"]
        self.assertEqual(confirmation["confirmed_reviews"], 2)
        self.assertEqual(confirmation["confirmed_tickets"], 1)
        self.assertEqual(confirmation["covered_items"], len(self.required))
        self.assertLessEqual(confirmation["rate"], 1.0)
        expected = len(self.required) / (len(self.result.tickets) * len(self.required))
        self.assertAlmostEqual(confirmation["rate"], expected, places=4)

    def test_unknown_ticket_review_does_not_inflate_rate(self) -> None:
        reviews = [
            {
                "ticket_id": "T-9999",
                "reviewer": "傅景",
                "decision": "confirm",
                "confirmed_items": list(self.required),
            }
        ]
        confirmation = self._metrics(reviews)["confirmation"]
        self.assertEqual(confirmation["covered_items"], 0)
        self.assertEqual(confirmation["rate"], 0.0)

    def test_fractional_minutes_are_not_truncated(self) -> None:
        target = next(t for t in self.result.tickets if t.handling_minutes == 25)
        tickets = [
            replace(t, handling_minutes=25.7) if t.ticket_id == target.ticket_id else t
            for t in self.result.tickets
        ]
        handling = self._metrics([], tickets)["handling"]
        self.assertEqual(handling["total_minutes"], 452.7)
        self.assertEqual(handling["clean_total_minutes"], 407.7)


class PipelineRegressionTest(unittest.TestCase):
    def test_missing_export_files_leave_no_run_dir(self) -> None:
        with temp_dir() as base:
            export = Path(base, "export")
            export.mkdir()
            run_dir = Path(base, "run")
            with self.assertRaises(InputError) as ctx:
                execute(
                    export_dir=export,
                    run_dir=run_dir,
                    config_path=DEFAULT_CONFIG,
                    now=FIXED_NOW,
                )
            self.assertIn("缺少文件", str(ctx.exception))
            self.assertFalse(run_dir.exists(), "输入有误时不应留下半初始化的运行目录")

    def test_run_dir_occupied_by_file_is_input_error(self) -> None:
        with temp_dir() as base:
            held = Path(base, "afile")
            held.write_text("x", encoding="utf-8")
            with self.assertRaises(InputError) as ctx:
                execute(
                    export_dir=DEMO_EXPORT,
                    run_dir=held,
                    config_path=DEFAULT_CONFIG,
                    now=FIXED_NOW,
                )
            self.assertIn("不是目录", str(ctx.exception))


class CliRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = temp_dir()
        self.run_dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self) -> tuple[int, str, str]:
        return call_cli(
            "run",
            "--export",
            str(DEMO_EXPORT),
            "--run-dir",
            self.run_dir,
            "--config",
            str(DEFAULT_CONFIG),
            "--now",
            "2026-10-20 10:00",
        )

    def test_sample_rejects_non_positive_size(self) -> None:
        self.assertEqual(self._run()[0], 0)
        for size in ("0", "-3"):
            code, out, err = call_cli("sample", "--run-dir", self.run_dir, "--size", size)
            self.assertEqual(code, 2, out + err)
            self.assertIn("正整数", err)

    def test_report_while_paused_returns_three(self) -> None:
        self.assertEqual(self._run()[0], 0)
        code, _out, err = call_cli(
            "pause", "--run-dir", self.run_dir, "--actor", "傅景", "--reason", "回归测试"
        )
        self.assertEqual(code, 0, err)
        code, out, err = call_cli("report", "--run-dir", self.run_dir)
        self.assertEqual(code, 3, out + err)
        self.assertIn("暂停", out)
        self.assertEqual(
            call_cli("resume", "--run-dir", self.run_dir, "--actor", "傅景")[0], 0
        )

    def test_status_labels_queue_as_including_blocked(self) -> None:
        self.assertEqual(self._run()[0], 0)
        code, out, err = call_cli("status", "--run-dir", self.run_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("队列 11（含阻塞项）", out)
        self.assertIn("分派：可出草稿 5 / 人工 9 / 阻塞 2", out)

    def test_unusable_run_dir_reports_error_without_traceback(self) -> None:
        held = Path(self.run_dir, "afile")
        held.write_text("x", encoding="utf-8")
        code, out, err = call_cli(
            "run",
            "--export",
            str(DEMO_EXPORT),
            "--run-dir",
            str(held),
            "--config",
            str(DEFAULT_CONFIG),
        )
        self.assertEqual(code, 2, out + err)
        self.assertIn("不是目录", err)
        self.assertNotIn("Traceback", out + err)


if __name__ == "__main__":
    unittest.main()
