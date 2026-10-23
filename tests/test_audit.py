"""审计链测试：追加、校验、篡改定位。"""

from __future__ import annotations

import json
import unittest

from tests.helpers import FIXED_NOW, temp_dir

from expo_trace.core.audit import AuditLog
from expo_trace.core.store import RunStore


class AuditChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = temp_dir()
        self.store = RunStore(self._tmp.name)
        self.store.ensure()
        self.log = AuditLog(self.store, actor="傅景")
        for index in range(3):
            self.log.append("test_action", f"S-{index}", {"index": index}, at=FIXED_NOW)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_chain_verifies(self) -> None:
        result = self.log.verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["entries"], 3)
        self.assertIsNone(result["broken_at"])

    def test_tampering_is_located(self) -> None:
        lines = self.store.read_text("audit.jsonl").splitlines()
        row = json.loads(lines[1])
        row["details"]["index"] = 99
        lines[1] = json.dumps(row, ensure_ascii=False)
        self.store.write_text("audit.jsonl", "\n".join(lines) + "\n")
        result = AuditLog(self.store, actor="傅景").verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], 2)

    def test_entries_keep_actor_and_subject(self) -> None:
        entries = self.log.read()
        self.assertEqual([row["seq"] for row in entries], [1, 2, 3])
        self.assertEqual(entries[0]["actor"], "傅景")
        self.assertEqual(entries[0]["prev"], "0" * 64)
        self.assertEqual(entries[1]["prev"], entries[0]["hash"])


if __name__ == "__main__":
    unittest.main()
