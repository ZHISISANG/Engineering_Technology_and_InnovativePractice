"""运行目录存储：原子写入、输入快照、暂停开关。

运行目录结构::

    runs/<name>/
    ├── input/            # 本次运行的输入副本（复现依据）
    ├── config.yaml       # 本次运行的配置快照
    ├── output/           # 工单、版本、草稿、队列、报告
    ├── snapshots/last/   # 上次成功读取的原始导出（读取失败时兜底）
    ├── reviews.jsonl     # 人工确认记录（只追加）
    ├── audit.jsonl       # 审计哈希链（只追加）
    ├── paused.json       # 存在即表示试点暂停
    └── manifest.json     # 输入哈希、降级标记、计数
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from expo_trace.utils.errors import InputError, SnapshotFallback, TraceError
from expo_trace.utils.jsons import dumps, jsonable
from expo_trace.utils.textutil import sha256_bytes

SNAPSHOT_DIR = "snapshots/last"
SNAPSHOT_META = "meta.json"


class RunStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ---- 基础 ----
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def ensure(self) -> None:
        for rel in ("", "input", "output", SNAPSHOT_DIR):
            self.path(rel).mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.root.is_dir()

    def _atomic_write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)

    def write_text(self, rel: str, text: str) -> Path:
        target = self.path(rel)
        self._atomic_write(target, text.encode("utf-8"))
        return target

    def read_text(self, rel: str) -> str:
        target = self.path(rel)
        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            raise InputError(f"读取失败：{target}（{exc}）") from exc

    def write_json(self, rel: str, obj: Any) -> Path:
        return self.write_text(rel, dumps(obj) + "\n")

    def read_json(self, rel: str) -> Any:
        try:
            return json.loads(self.read_text(rel))
        except json.JSONDecodeError as exc:
            raise InputError(f"JSON 损坏：{self.path(rel)}（{exc}）") from exc

    def write_jsonl(self, rel: str, rows: Iterable[Any]) -> Path:
        text = "".join(json.dumps(jsonable(row), ensure_ascii=False) + "\n" for row in rows)
        return self.write_text(rel, text)

    def append_jsonl(self, rel: str, row: Any) -> Path:
        target = self.path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(jsonable(row), ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return target

    def read_jsonl(self, rel: str) -> list[dict[str, Any]]:
        target = self.path(rel)
        if not target.exists():
            return []
        rows: list[dict[str, Any]] = []
        for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise InputError(f"{target} 第 {lineno} 行不是合法 JSON：{exc}") from exc
        return rows

    # ---- 输入快照 ----
    def copy_inputs(self, files: dict[str, str], source_dir: Path) -> dict[str, str]:
        """把输入复制到 run/input，返回文件名 → sha256。"""
        hashes: dict[str, str] = {}
        for name in files:
            src = Path(source_dir) / name
            if not src.is_file():
                raise InputError(f"缺少输入文件：{src}")
            data = src.read_bytes()
            self._atomic_write(self.path("input", name), data)
            hashes[name] = sha256_bytes(data)
        return hashes

    def save_snapshot(self, files: dict[str, str], source_dir: Path, meta: dict[str, Any]) -> None:
        """保存“上次成功读取”的原始导出，供读取失败时兜底。"""
        snap = self.path(SNAPSHOT_DIR)
        if snap.exists():
            shutil.rmtree(snap, ignore_errors=True)
        snap.mkdir(parents=True, exist_ok=True)
        for name in files:
            data = (Path(source_dir) / name).read_bytes()
            self._atomic_write(snap / name, data)
        payload = dict(meta)
        payload["files"] = list(files)
        self._atomic_write(snap / SNAPSHOT_META, dumps(payload).encode("utf-8"))

    def load_snapshot(self) -> tuple[dict[str, str], dict[str, Any]]:
        snap = self.path(SNAPSHOT_DIR)
        meta_path = snap / SNAPSHOT_META
        if not meta_path.is_file():
            raise SnapshotFallback("没有可用的上次快照")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotFallback(f"快照元数据损坏：{exc}") from exc
        files: dict[str, str] = {}
        for name in meta.get("files", []):
            file_path = snap / name
            try:
                files[name] = file_path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                raise SnapshotFallback(f"快照文件缺失：{name}（{exc}）") from exc
        if not files:
            raise SnapshotFallback("快照为空")
        return files, meta

    def snapshot_at(self) -> str:
        try:
            meta = json.loads(self.path(SNAPSHOT_DIR, SNAPSHOT_META).read_text(encoding="utf-8"))
            return str(meta.get("saved_at", ""))
        except (OSError, json.JSONDecodeError):
            return ""

    # ---- 暂停开关 ----
    def is_paused(self) -> bool:
        return self.path("paused.json").is_file()

    def pause_info(self) -> dict[str, Any]:
        if not self.is_paused():
            return {}
        try:
            return self.read_json("paused.json")
        except TraceError:
            return {"reason": "暂停标记存在但无法读取"}

    def pause(self, actor: str, reason: str, now: datetime) -> None:
        self.write_json(
            "paused.json",
            {"actor": actor, "reason": reason, "at": now.isoformat(timespec="seconds")},
        )

    def resume(self) -> None:
        target = self.path("paused.json")
        if target.exists():
            target.unlink()

    # ---- 复核记录 ----
    def reviews(self) -> list[dict[str, Any]]:
        return self.read_jsonl("reviews.jsonl")

    def append_review(self, record: Any) -> None:
        self.append_jsonl("reviews.jsonl", record)
