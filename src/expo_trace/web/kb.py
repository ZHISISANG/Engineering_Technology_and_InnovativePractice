"""知识库：Markdown 切片、向量化、入库与混合检索。

切片直接复用 `src/rag_qa.py` 的结构感知切片（按标题分节、节内滑窗），
保证“引用到节”的行为与阶段 1 原型一致。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Sequence

from .. import rag_qa
from ..utils.textutil import sha256_bytes
from .store import Store


@dataclass
class KbHit:
    chunk_id: str
    doc_id: str
    title: str
    heading: str
    text: str
    score: float
    version_label: str
    effective_from: str
    effective_to: str


def embedder(provider: str = "hash") -> rag_qa.Embedder:
    return rag_qa.Embedder(provider)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if not na or not nb else dot / (na * nb)


def _overlap(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    tokens = set(rag_qa.tokenize(text))
    return len(query_tokens & tokens) / len(query_tokens)


def ingest_markdown(
    store: Store,
    *,
    text: str,
    filename: str,
    title: str,
    uploaded_by: str,
    scope: str = "all",
    version_label: str = "",
    effective_from: str = "",
    effective_to: str = "",
    size: int = 500,
    overlap: int = 80,
    provider: str = "hash",
) -> tuple[str, int]:
    chunks = rag_qa.chunk_text(text, doc=filename, size=size, overlap=overlap)
    if not chunks:
        raise ValueError("文档为空，未生成切片")
    vectors = embedder(provider).embed_documents([c.text for c in chunks])
    rows = [
        {"heading": c.heading or "正文", "text": c.text, "vector": list(map(float, vec)),
         "tokens": rag_qa.tokenize(c.text)}
        for c, vec in zip(chunks, vectors)
    ]
    doc_id = store.kb_add(
        title=title or filename, filename=filename,
        sha256=sha256_bytes(text.encode("utf-8")), chunks=rows, scope=scope,
        version_label=version_label, effective_from=effective_from, effective_to=effective_to,
        size=len(text.encode("utf-8")), uploaded_by=uploaded_by,
    )
    return doc_id, len(rows)


def search(
    store: Store,
    query: str,
    *,
    top_k: int = 3,
    scope: str = "",
    moment: str = "",
    provider: str = "hash",
) -> list[KbHit]:
    """向量余弦 0.6 + 词面覆盖 0.4；scope/moment 是硬过滤，先过滤再排序。"""
    rows = store.kb_active_chunks()
    if not rows:
        return []
    if scope:
        rows = [r for r in rows if r["scope"] in ("all", scope)]
    if moment:
        rows = [r for r in rows
                if (not r["effective_from"] or r["effective_from"] <= moment)
                and (not r["effective_to"] or r["effective_to"] >= moment)]
    if not rows:
        return []
    qvec = embedder(provider).embed_query(query)
    qtokens = set(rag_qa.tokenize(query))
    hits: list[KbHit] = []
    for row in rows:
        vec = json.loads(row["vector"])
        score = 0.6 * _cosine(qvec, vec) + 0.4 * _overlap(qtokens, row["text"])
        hits.append(KbHit(
            chunk_id=row["id"], doc_id=row["doc_id"], title=row["title"],
            heading=row["heading"], text=row["text"], score=round(score, 4),
            version_label=row["version_label"], effective_from=row["effective_from"],
            effective_to=row["effective_to"],
        ))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def render_context(hits: Sequence[KbHit]) -> str:
    blocks = []
    for i, hit in enumerate(hits, start=1):
        meta = f"《{hit.title}》{hit.heading}"
        if hit.version_label:
            meta += f"｜版本 {hit.version_label}"
        if hit.effective_from or hit.effective_to:
            meta += f"｜有效期 {hit.effective_from[:10]}~{(hit.effective_to or '长期')[:10]}"
        blocks.append(f"[{i}] {meta}\n{hit.text}")
    return "\n\n".join(blocks)
