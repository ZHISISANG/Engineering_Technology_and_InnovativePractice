"""JSON 序列化：把 dataclass / datetime / Enum 转成可写入文件的结构。"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def jsonable(obj: Any) -> Any:
    """递归转换为 JSON 可编码对象（不改变原对象）。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def canonical_json(obj: Any) -> str:
    """稳定序列化：用于哈希与审计链，键排序、无多余空格。"""
    return json.dumps(jsonable(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dumps(obj: Any, *, indent: int | None = 2) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False, indent=indent)
