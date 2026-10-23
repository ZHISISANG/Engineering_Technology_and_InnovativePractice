"""装载测试：重复编号、双时间戳、脱敏、快照兜底。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.helpers import DEFAULT_CONFIG, DEMO_EXPORT, FIXED_NOW, run_demo, temp_dir

from expo_trace.core.codes import Code
from expo_trace.core.config import load_config
from expo_trace.core.ingest import EXPORT_FILES, MANIFEST_FILE, ingest
from expo_trace.core.store import RunStore
from expo_trace.utils.errors import InputError


class DemoIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_ticket_count_and_duplicate_retained(self) -> None:
        tickets = self.result.tickets
        self.assertEqual(len(tickets), 16)
        # 同一工单号只保留首行进入判定，重复行不能被静默丢弃：原行留在问题记录里。
        duplicated = [t for t in tickets if t.ticket_id == "T-1004"]
        self.assertEqual(len(duplicated), 1)
        issues = [i for i in self.result.issues if i.code == Code.DUPLICATE_ID and i.ticket_id == "T-1004"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].raw.get("工单编号"), "T-1004")
        self.assertIn("本行", issues[0].detail)

    def test_pii_is_masked_everywhere(self) -> None:
        for ticket in self.result.tickets:
            self.assertIn("*", ticket.contact)
            dumped = json.dumps(ticket.raw, ensure_ascii=False)
            self.assertNotIn("1380000", dumped)

    def test_versions_loaded(self) -> None:
        self.assertEqual(len(self.result.ingest.versions), 12)
        self.assertTrue(all(v.version_id for v in self.result.ingest.versions))

    def test_double_timestamp_and_backfill_flag(self) -> None:
        by_id = {t.ticket_id: t for t in self.result.tickets}
        late = by_id["T-1007"]
        self.assertTrue(late.occurred.ok)
        self.assertTrue(late.received.ok)
        self.assertTrue(late.backfilled)
        self.assertGreater(late.backfill_gap or 0, 8)

    def test_duration_unit_is_normalized_with_note(self) -> None:
        by_id = {t.ticket_id: t for t in self.result.tickets}
        self.assertEqual(by_id["T-1010"].handling_minutes, 90.0)
        self.assertIn("小时", by_id["T-1010"].handling_note)


class SnapshotFallbackTest(unittest.TestCase):
    def test_missing_input_falls_back_to_snapshot(self) -> None:
        with temp_dir() as tmp:
            run_demo(tmp)
            store = RunStore(tmp)
            store.path("input", "service_desk.csv").unlink()
            config = load_config(DEFAULT_CONFIG)
            result = ingest(store, config, FIXED_NOW)
            self.assertTrue(result.degraded)
            self.assertTrue(result.snapshot_at)
            self.assertIn("快照", result.degraded_reason)
            self.assertEqual(len(result.tickets), 16)

    def test_missing_input_without_snapshot_is_an_error(self) -> None:
        with temp_dir() as tmp:
            store = RunStore(tmp)
            store.ensure()
            config = load_config(DEFAULT_CONFIG)
            with self.assertRaises(InputError):
                ingest(store, config, FIXED_NOW)

    def test_writable_manifest_is_refused(self) -> None:
        with temp_dir() as tmp:
            store = RunStore(tmp)
            store.ensure()
            for name in EXPORT_FILES:
                store.write_text(f"input/{name}", (DEMO_EXPORT / name).read_text(encoding="utf-8"))
            manifest = json.loads((DEMO_EXPORT / MANIFEST_FILE).read_text(encoding="utf-8"))
            manifest["read_only"] = False
            store.write_text(f"input/{MANIFEST_FILE}", json.dumps(manifest, ensure_ascii=False))
            config = load_config(DEFAULT_CONFIG)
            with self.assertRaises(InputError):
                ingest(store, config, FIXED_NOW)


if __name__ == "__main__":
    unittest.main()
