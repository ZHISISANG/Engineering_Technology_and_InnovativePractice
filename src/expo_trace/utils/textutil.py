"""文本与脱敏工具。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_ID_CARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
_SPACE = re.compile(r"[ \t\u3000]+")


def mask_pii(text: str) -> str:
    """脱敏手机号、邮箱、身份证号；幂等。"""
    if not text:
        return text
    text = _PHONE.sub(r"\1****\3", text)
    text = _EMAIL.sub(r"\1***\2", text)
    text = _ID_CARD.sub(r"\1********\2", text)
    return text


def collapse_ws(text: str) -> str:
    return _SPACE.sub(" ", text or "").strip()


def normalize_key(text: str) -> str:
    """用于问题分类/关键词比对：去空白、全角转半角、小写。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    return collapse_ws(normalized).lower()


def contains_any(text: str, keywords: list[str] | tuple[str, ...]) -> list[str]:
    haystack = normalize_key(text)
    return [kw for kw in keywords if normalize_key(kw) and normalize_key(kw) in haystack]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 12) -> str:
    """确定性短 ID：同样的输入在多次运行中得到同样的编号。"""
    seed = "|".join(str(p) for p in parts)
    return f"{prefix}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:length]}"


def truncate(text: str, limit: int = 120) -> str:
    text = collapse_ws(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
