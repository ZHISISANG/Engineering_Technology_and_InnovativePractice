"""只追加的审计日志：谁在什么时候对哪条工单做了什么。

审计要求（f45）：能看到谁查过工单、用了哪个版本、提了什么建议、谁确认，
不能只保留最终摘要。因此每条记录都包含 actor、action、subject 与关键细节，
并用哈希链把记录串起来，任何一条被改动都能定位到断点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from expo_trace.core.store import RunStore
from expo_trace.utils.jsons import canonical_json
from expo_trace.utils.textutil import sha256_bytes
from expo_trace.utils.timeutil import iso

GENESIS = "0" * 64
AUDIT_FILE = "audit.jsonl"


class AuditLog:
    def __init__(self, store: RunStore, actor: str) -> None:
        self.store = store
        self.actor = actor

    def read(self) -> list[dict[str, Any]]:
        return self.store.read_jsonl(AUDIT_FILE)

    def append(
        self,
        action: str,
        subject: str,
        details: dict[str, Any] | None = None,
        at: datetime | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        entries = self.read()
        seq = len(entries) + 1
        prev = entries[-1]["hash"] if entries else GENESIS
        payload = {
            "seq": seq,
            "at": iso(at or datetime.now().astimezone()) or "",
            "actor": actor or self.actor,
            "action": action,
            "subject": subject,
            "details": details or {},
            "prev": prev,
        }
        entry = dict(payload)
        entry["hash"] = sha256_bytes(canonical_json(payload).encode("utf-8"))
        self.store.append_jsonl(AUDIT_FILE, entry)
        return entry

    def verify(self) -> dict[str, Any]:
        entries = self.read()
        prev = GENESIS
        for entry in entries:
            payload = {k: entry.get(k) for k in ("seq", "at", "actor", "action", "subject", "details", "prev")}
            expected = sha256_bytes(canonical_json(payload).encode("utf-8"))
            if entry.get("prev") != prev:
                return {
                    "ok": False,
                    "entries": len(entries),
                    "broken_at": entry.get("seq"),
                    "reason": "prev 指针与上一条记录不一致（记录被删除或重排）",
                }
            if entry.get("hash") != expected:
                return {
                    "ok": False,
                    "entries": len(entries),
                    "broken_at": entry.get("seq"),
                    "reason": "记录内容与哈希不一致（记录被修改）",
                }
            prev = entry["hash"]
        return {"ok": True, "entries": len(entries), "broken_at": None, "reason": ""}
