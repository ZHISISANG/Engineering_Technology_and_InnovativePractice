"""把 CLI 运行产物读成只读问答上下文。

Web 层不重新实现版本判定：`runs/<name>/output/resolutions.json` 已经是
CLI 用同一套规则算出来的结果。这里只做索引与查询，避免两套判定漂移。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..utils.errors import InputError


class Dataset:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        out = self.run_dir / "output"
        self.tickets: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}
        self.resolutions: dict[str, dict[str, Any]] = {}
        self.handoffs: dict[str, dict[str, Any]] = {}
        self._load(out)

    def _load(self, out: Path) -> None:
        if not (out / "tickets.jsonl").exists():
            raise InputError(f"运行目录缺少 output/tickets.jsonl：{out}")
        for line in (out / "tickets.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                self.tickets[row["ticket_id"]] = row
        for line in (out / "versions.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                self.versions[row["version_id"]] = row
        self.resolutions = json.loads((out / "resolutions.json").read_text(encoding="utf-8"))
        handoff_csv = self.run_dir / "input" / "handoff.csv"
        if handoff_csv.exists():
            with handoff_csv.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    tid = row.get("ticket_id") or row.get("工单编号") or ""
                    if tid:
                        self.handoffs[tid] = dict(row)

    # -- 查询 -------------------------------------------------------------
    def ticket(self, ticket_id: str) -> dict[str, Any] | None:
        return self.tickets.get(ticket_id)

    def resolution(self, ticket_id: str) -> dict[str, Any] | None:
        return self.resolutions.get(ticket_id)

    def version(self, version_id: str) -> dict[str, Any] | None:
        return self.versions.get(version_id)

    def current(self, ticket_id: str) -> dict[str, Any] | None:
        res = self.resolution(ticket_id)
        if not res or res.get("state") != "current" or not res.get("version_id"):
            return None
        return self.version(res["version_id"])

    def search_versions(self, batch: str, category: str, moment: str = "") -> list[dict[str, Any]]:
        rows = [v for v in self.versions.values()
                if v.get("batch") == batch and v.get("category") == category]
        if moment:
            rows = [v for v in rows if v["effective_from"] <= moment
                    and (not v.get("effective_to") or v["effective_to"] >= moment)]
        return sorted(rows, key=lambda v: v["effective_from"], reverse=True)

    def unconfirmed_items(self, ticket_id: str) -> list[str]:
        ticket = self.ticket(ticket_id) or {}
        items: list[str] = []
        occurred = (ticket.get("occurred") or {}).get("state")
        received = (ticket.get("received") or {}).get("state")
        if occurred != "ok":
            items.append(f"业务发生时间状态为 {occurred}，不能作为对外时间依据")
        if received != "ok":
            items.append(f"系统接收时间状态为 {received}")
        res = self.resolution(ticket_id) or {}
        for row in res.get("considered", []) or []:
            if row.get("state") == "unconfirmed":
                items.append(f"{row.get('version_id')} 未确认，未参与判定")
        return items

    def handoff(self, ticket_id: str) -> dict[str, Any] | None:
        return self.handoffs.get(ticket_id)
