#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报销制度「裸问答」原型（阶段 1，单文件、无 Web）
=================================================

一条命令完成：读 Markdown 制度 → 500 字切片 → 向量化 → 建索引（FAISS 可选）
→ 命令行提问 → BM25 + 向量混合检索 top3 → 拼提示词 → 调 LLM → 带引用的回答。

设计约束（与《v2 架构选型方案》一致）：
- 只用标准库 + numpy；FAISS 是可选加速项，缺省时自动退化为 numpy 精确检索，
  两者返回的 top-k 与分数一致（本文件用同一套归一化与排序）。
- LLM 调用集中在 `call_llm()`，供应商由 `--provider` / 环境变量决定，
  业务代码不出现任何厂商 SDK。
- 检索不到依据时不调用模型，直接回答“资料中没有相关规定”，避免编造。

复用标记：带 `[核心·阶段2复用]` 的函数/类会被阶段 2（FastAPI 服务）直接复用；
带 `[原型专用]` 的只在命令行入口使用。

用法：
    python rag_qa.py --selftest                       # 离线自检，不联网
    python rag_qa.py --question "出差住宿标准是多少"     # 默认 mock 供应商
    python rag_qa.py -q "..." --provider deepseek      # 需 DEEPSEEK_API_KEY
    python rag_qa.py -q "..." --show-prompt --json     # 查看提示词与结构化结果
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# ─────────────────────────────────────────────────────────────────────────────
# 0. 依赖探测
#    numpy 是硬依赖（向量运算）；faiss 是可选依赖，缺失时自动降级。
#    `[核心·阶段2复用]`：降级策略在服务端同样适用，保证小数据量也能跑。
# ─────────────────────────────────────────────────────────────────────────────
try:
    import numpy as np
except ImportError:  # pragma: no cover - 环境问题，直接给出可执行的修复命令
    sys.stderr.write("缺少 numpy，请先执行：python -m pip install numpy\n")
    raise SystemExit(2)

try:
    import faiss  # type: ignore

    HAS_FAISS = True
except ImportError:  # faiss 不是必需依赖
    faiss = None  # type: ignore
    HAS_FAISS = False


# ─────────────────────────────────────────────────────────────────────────────
# 1. 数据结构
#    Chunk 是检索与引用的最小单位；Hit 是一次检索命中的完整解释（两路分数都保留，
#    便于前端展示“为什么是它”，也便于阶段 2 做可解释性审计）。
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Chunk:
    """一个文本切片。chunk_id 稳定（由文件、序号、内容哈希决定），可跨次运行引用。"""

    chunk_id: str
    doc: str
    heading: str
    text: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc": self.doc,
            "heading": self.heading,
            "text": self.text,
            "start": self.start,
            "end": self.end,
        }


@dataclass
class Hit:
    """一条检索结果：chunk 原文 + BM25 分 + 向量分 + 融合分。"""

    chunk: Chunk
    score: float
    bm25: float
    vector: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "heading": self.chunk.heading,
            "score": round(self.score, 4),
            "bm25": round(self.bm25, 4),
            "vector": round(self.vector, 4),
            "preview": self.chunk.text[:120].replace("\n", " "),
        }


@dataclass
class LLMResult:
    """LLM 返回的统一结构；阶段 2 直接落库到 messages 表。"""

    text: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0
    raw: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


class LLMError(RuntimeError):
    """LLM 调用失败的统一异常；上层据此决定“报错”而不是“编一个答案”。"""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Markdown 切片（500 字 + 80 字重叠）
#    [核心·阶段2复用]
#    切片的三个要求：
#    a) 尽量在句号/换行处断开，避免把一句话切成两半；
#    b) 相邻切片保留 overlap 个字符，防止答案正好落在切口；
#    c) 记录 heading，回答引用时能显示“住宿标准”而不是干巴巴的 [1]。
# ─────────────────────────────────────────────────────────────────────────────
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_END = ("。", "！", "？", "\n")


def _headings(text: str) -> list[tuple[int, str]]:
    """返回 (字符偏移, 标题文本) 列表，用于给切片标注所属章节。"""
    return [(m.start(), m.group(2).strip()) for m in _HEADING_RE.finditer(text)]


def _heading_at(heads: list[tuple[int, str]], pos: int) -> str:
    """取 pos 之前最近的一个标题；没有标题时归入“正文”。"""
    current = "正文"
    for start, title in heads:
        if start <= pos:
            current = title
        else:
            break
    return current


def chunk_text(
    text: str,
    *,
    doc: str = "policy.md",
    size: int = 500,
    overlap: int = 80,
) -> list[Chunk]:
    """
    结构感知的定长切片：先按 Markdown 标题分节，再在节内做 500 字滑窗。

    为什么不能直接对全文滑窗：制度文档的语义边界是“条”与“节”，
    全文滑窗会把“二、出差申请”的结尾和“三、住宿标准”的开头塞进同一片，
    检索时这一片既像 A 又像 B，排序会压过真正讲住宿的那一片。

    规则
    ----
    1. 每个标题开启一个新节，节内单独滑窗，切片不跨节；
    2. 单片不超过 size 个字符，优先在句号/换行处断开；
    3. 相邻切片重叠 overlap 个字符，防止答案落在切口；
    4. heading 记录所属节标题，引用时可直接显示“三、住宿标准”。
    """
    if size <= 0:
        raise ValueError("size 必须为正整数")
    if not 0 <= overlap < size:
        raise ValueError("overlap 必须满足 0 <= overlap < size")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    heads = _headings(text)
    n = len(text)

    # 把全文切成 (标题, 起, 止)：标题之前的内容归入“正文”
    sections: list[tuple[str, int, int]] = []
    if not heads:
        sections.append(("正文", 0, n))
    else:
        if heads[0][0] > 0:
            sections.append(("正文", 0, heads[0][0]))
        for idx, (pos, title) in enumerate(heads):
            stop = heads[idx + 1][0] if idx + 1 < len(heads) else n
            sections.append((title, pos, stop))

    chunks: list[Chunk] = []

    def emit(title: str, start: int, stop: int) -> None:
        body = text[start:stop].strip()
        if len(body) < 40 and chunks:
            return  # 丢掉标题下的空节/分隔线碎片
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
        chunks.append(
            Chunk(
                chunk_id=f"{Path(doc).stem}-{len(chunks):03d}-{digest}",
                doc=doc,
                heading=title,
                text=body,
                start=start,
                end=stop,
            )
        )

    for title, sec_start, sec_end in sections:
        if sec_end - sec_start <= size:
            emit(title, sec_start, sec_end)
            continue

        i = sec_start
        while i < sec_end:
            end = min(i + size, sec_end)
            if end < sec_end:
                window = text[i:end]
                cut = max(window.rfind(mark) for mark in _SENTENCE_END)
                if cut >= int(size * 0.5):
                    end = i + cut + 1
            emit(title, i, end)
            if end >= sec_end:
                break
            i = max(end - overlap, i + 1)

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# 3. 分词与 BM25
#    [核心·阶段2复用]
#    中文不引入 jieba 这类词典依赖：用“单字 + 相邻双字”作为词项，
#    对“住宿标准”“报销时限”这类制度术语足够稳，且离线可用。
#    BM25 用标准 Okapi 公式，参数 k1=1.5、b=0.75。
# ─────────────────────────────────────────────────────────────────────────────
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """
    混合分词：英文/数字按词，中文按“单字 + 双字”。

    例：“住宿标准 650 元” → ['650', '住', '宿', '标', '准', '元',
                              '住宿', '宿标', '标准', '准元']
    """
    lowered = text.lower()
    latin = _LATIN_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return latin + cjk + bigrams


class BM25:
    """
    Okapi BM25。

    score(q, d) = Σ idf(t) · f(t,d)·(k1+1) / (f(t,d) + k1·(1-b+b·|d|/avgdl))
    idf(t)      = ln( (N - df + 0.5) / (df + 0.5) + 1 )
    """

    def __init__(self, corpus_tokens: Sequence[Sequence[str]], *, k1: float = 1.5,
                 b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [list(toks) for toks in corpus_tokens]
        self.n_docs = len(self.docs)
        self.doc_len = np.array([len(d) for d in self.docs], dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if self.n_docs else 0.0

        # 词项 → 文档频率（有多少篇文档出现过）
        df: dict[str, int] = {}
        for doc in self.docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1

        # 预先算好 idf，查询时只做查表
        self.idf: dict[str, float] = {
            term: math.log((self.n_docs - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in df.items()
        }
        # 词频表：term → {doc_index: tf}
        self.tf: dict[str, dict[int, int]] = {}
        for idx, doc in enumerate(self.docs):
            for term in doc:
                self.tf.setdefault(term, {})
                self.tf[term][idx] = self.tf[term].get(idx, 0) + 1

    def scores(self, query_tokens: Iterable[str]) -> np.ndarray:
        """返回每个文档的 BM25 分（未归一化，后续与向量分一起做 min-max）。"""
        result = np.zeros(self.n_docs, dtype=np.float32)
        if not self.n_docs or self.avgdl <= 0:
            return result

        for term in query_tokens:
            postings = self.tf.get(term)
            if not postings:
                continue
            idf = self.idf.get(term, 0.0)
            for doc_idx, freq in postings.items():
                denom = freq + self.k1 * (
                    1.0 - self.b + self.b * self.doc_len[doc_idx] / self.avgdl
                )
                result[doc_idx] += idf * freq * (self.k1 + 1.0) / denom
        return result


# ─────────────────────────────────────────────────────────────────────────────
# 4. 向量化
#    [核心·阶段2复用]
#    两种 provider：
#    - hash  ：离线、确定性、零成本；只做“字面相似”，不具备语义能力，
#              用于本地跑通链路与单元测试，不用于生产。
#    - openai：调用 /v1/embeddings（OpenAI 兼容端点，DeepSeek/通义等同样适用）。
#    向量统一 L2 归一化，因此内积 = 余弦相似度，FAISS 用 IndexFlatIP。
# ─────────────────────────────────────────────────────────────────────────────
EMBED_DIM = 512


def _hash_embed_one(text: str, dim: int) -> np.ndarray:
    """
    特征哈希（hashing trick）：
    每个“英文词 / 中文双字”映射到 [0, dim) 的一个桶，正负号由哈希位决定，
    再做 L2 归一化。相同文本永远得到相同向量，便于自检与缓存。
    """
    vec = np.zeros(dim, dtype=np.float32)
    lowered = text.lower()
    feats = _LATIN_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    feats += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    feats += cjk

    for feat in feats:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + (digest[5] % 3)  # 轻微加权，降低高频单字的支配
        vec[idx] += sign * weight

    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


class Embedder:
    """
    统一 embedding 接口。`embed_documents` 与 `embed_query` 分开，
    是因为真实供应商对“文档/查询”可能使用不同的指令前缀或模型。
    """

    def __init__(self, provider: str = "hash", *, model: str = "", dim: int = EMBED_DIM,
                 timeout: int = 60) -> None:
        self.provider = provider
        self.model = model or ("text-embedding-3-small" if provider == "openai" else "hash")
        self.dim = dim
        self.timeout = timeout

    # -- 公共入口 ---------------------------------------------------------
    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if self.provider == "hash":
            return np.vstack([_hash_embed_one(t, self.dim) for t in texts])
        if self.provider == "openai":
            return self._openai_embed(list(texts))
        raise ValueError(f"不支持的 embedding provider: {self.provider}")

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]

    # -- OpenAI 兼容实现 ---------------------------------------------------
    def _openai_embed(self, texts: list[str]) -> np.ndarray:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise LLMError("embedding=openai 需要设置环境变量 OPENAI_API_KEY")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        vectors: list[list[float]] = []

        # 分批请求，避免单次 body 过大
        for start in range(0, len(texts), 64):
            batch = texts[start:start + 64]
            payload = json.dumps({"model": self.model, "input": batch}).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/embeddings",
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                raise LLMError(f"embedding 请求失败：HTTP {exc.code} {detail}") from exc
            except urllib.error.URLError as exc:
                raise LLMError(f"embedding 网络错误：{exc.reason}") from exc

            ordered = sorted(data["data"], key=lambda item: item["index"])
            vectors.extend(item["embedding"] for item in ordered)

        arr = np.asarray(vectors, dtype=np.float32)
        # 归一化后再入库，保证内积即余弦
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


# ─────────────────────────────────────────────────────────────────────────────
# 5. 向量索引（FAISS 优先，numpy 兜底）
#    [核心·阶段2复用]
#    FAISS 的 IndexFlatIP 做的是内积；因为向量已归一化，内积等于余弦相似度。
#    数据量小（几千片以内）时 numpy 的矩阵乘法同样快，且少一个二进制依赖，
#    所以这里保留两条路径，并用 `backend` 字段记录实际使用的是哪一个。
# ─────────────────────────────────────────────────────────────────────────────
class VectorStore:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, *, backend: str) -> None:
        if len(chunks) != vectors.shape[0]:
            raise ValueError("chunks 与 vectors 数量不一致")
        self.chunks = chunks
        self.vectors = vectors.astype(np.float32)
        self.backend = backend

        # FAISS 分支：构建内积索引，ID 直接使用数组下标
        self.index = None
        if backend == "faiss":
            self.index = faiss.IndexFlatIP(self.vectors.shape[1])  # type: ignore[union-attr]
            self.index.add(self.vectors)

    # -- 构建 -------------------------------------------------------------
    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder) -> "VectorStore":
        if not chunks:
            raise ValueError("没有可索引的切片")
        vectors = embedder.embed_documents([c.text for c in chunks])
        backend = "faiss" if HAS_FAISS else "numpy"
        return cls(chunks, vectors, backend=backend)

    # -- 检索 -------------------------------------------------------------
    def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[int, float]]:
        """返回 [(chunk_index, cosine_score), ...]，按分数降序。"""
        if not self.chunks:
            return []
        top_k = min(top_k, len(self.chunks))
        q = query_vec.reshape(1, -1).astype(np.float32)

        if self.index is not None:
            scores, ids = self.index.search(q, top_k)  # type: ignore[union-attr]
            return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]

        sims = (self.vectors @ q[0]).astype(np.float32)
        order = np.argsort(-sims)[:top_k]
        return [(int(i), float(sims[i])) for i in order]

    # -- 持久化 -----------------------------------------------------------
    def save(self, index_dir: Path, meta: dict[str, Any]) -> None:
        """
        落盘三件套：
          chunks.json  —— 切片原文与位置
          vectors.npy  —— 归一化向量（numpy 兜底路径也用它，便于换机复现）
          index.faiss  —— 仅 FAISS 分支生成
          meta.json    —— 制度文件哈希、切片参数、embedding 配置
        """
        index_dir.mkdir(parents=True, exist_ok=True)
        (index_dir / "chunks.json").write_text(
            json.dumps([c.to_dict() for c in self.chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        np.save(index_dir / "vectors.npy", self.vectors)
        if self.index is not None:
            faiss.write_index(self.index, str(index_dir / "index.faiss"))  # type: ignore[union-attr]
        meta = {**meta, "backend": self.backend, "count": len(self.chunks),
                "dim": int(self.vectors.shape[1])}
        (index_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, index_dir: Path) -> tuple["VectorStore", dict[str, Any]]:
        meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
        raw_chunks = json.loads((index_dir / "chunks.json").read_text(encoding="utf-8"))
        chunks = [Chunk(**item) for item in raw_chunks]
        vectors = np.load(index_dir / "vectors.npy")

        backend = meta.get("backend", "numpy")
        if backend == "faiss" and not HAS_FAISS:
            # 换到没有 faiss 的机器：用已保存的向量重建 numpy 路径，结果不受影响
            backend = "numpy"
        store = cls(chunks, vectors, backend=backend)
        if backend == "faiss" and (index_dir / "index.faiss").exists():
            store.index = faiss.read_index(str(index_dir / "index.faiss"))  # type: ignore[union-attr]
        return store, meta


# ─────────────────────────────────────────────────────────────────────────────
# 6. 混合检索（BM25 + 向量，分数各归一化后加权）
#    [核心·阶段2复用]
#    为什么不直接把两种分数相加：BM25 是无上界分值，向量余弦在 [-1,1]，
#    量纲不同会互相淹没。这里各自做 min-max 归一化到 [0,1]，再按 alpha 加权。
#    alpha=0.5 表示两路同等重要，可用 --alpha 调整。
# ─────────────────────────────────────────────────────────────────────────────
def _minmax(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    lo, hi = float(values.min()), float(values.max())
    if math.isclose(hi, lo):
        return np.zeros_like(values)
    return (values - lo) / (hi - lo)


def hybrid_search(
    question: str,
    *,
    chunks: list[Chunk],
    bm25: BM25,
    store: VectorStore,
    embedder: Embedder,
    top_k: int = 3,
    alpha: float = 0.5,
) -> list[Hit]:
    """BM25 与向量各取全量分数 → 归一化 → 加权融合 → 取 top_k。"""
    if not chunks:
        return []

    q_tokens = tokenize(question)
    bm25_scores = bm25.scores(q_tokens)
    q_vec = embedder.embed_query(question)

    # 向量路：先取全部（数量与 chunks 对齐），保证归一化口径一致
    vec_pairs = store.search(q_vec, top_k=len(chunks))
    vec_scores = np.zeros(len(chunks), dtype=np.float32)
    for idx, score in vec_pairs:
        vec_scores[idx] = score

    bm25_norm = _minmax(bm25_scores)
    vec_norm = _minmax(vec_scores)
    combined = alpha * vec_norm + (1.0 - alpha) * bm25_norm

    order = np.argsort(-combined)[:top_k]
    return [
        Hit(
            chunk=chunks[int(i)],
            score=float(combined[int(i)]),
            bm25=float(bm25_scores[int(i)]),
            vector=float(vec_scores[int(i)]),
        )
        for i in order
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 7. 提示词
#    [核心·阶段2复用]
#    提示词做三件事：限定知识边界（只能用资料）、强制标注引用编号、
#    明确“没有依据就拒答”。这三个约束比“请准确回答”有效得多。
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """你是公司报销制度的问答助手，服务对象是普通员工。

必须遵守：
1. 只能依据<资料>中的内容回答，不得使用资料之外的知识，也不得推测。
2. 每个结论后用 [编号] 标注依据，编号必须来自资料块编号。
3. 资料没有覆盖的问题，直接回答“资料中没有相关规定”，并说明缺哪类信息；不要编造数字、比例或期限。
4. 涉及金额、比例、时限时，引用原文数字，不得改写、换算或估算。
5. 不提供法律意见，不承诺审批结果；审批以 OA 流程为准。
6. 回答用简体中文，先给结论，再给依据，控制在 300 字以内。"""


def build_messages(question: str, hits: Sequence[Hit]) -> list[dict[str, str]]:
    """把检索结果拼成 chat messages；编号从 1 开始，与引用一一对应。"""
    blocks = []
    for i, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{i}] 标题：{hit.chunk.heading}\n"
            f"切片：{hit.chunk.chunk_id}\n"
            f"内容：{hit.chunk.text}"
        )
    material = "\n\n".join(blocks) if blocks else "（无检索结果）"
    user = (
        f"<资料>\n{material}\n</资料>\n\n"
        f"<问题>\n{question}\n</问题>\n\n"
        "请按系统提示的要求回答，并在句末标注 [编号]。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 8. LLM 调用（供应商可配置，独立函数）
#    [核心·阶段2复用]
#    统一走 OpenAI 兼容的 /chat/completions 协议：OpenAI、DeepSeek、通义、
#    本地 vLLM / Ollama 都能用同一段代码，切换只改 base_url + model + key。
#    mock 供应商用于离线演示与单元测试，输出明确标注“未调用真实模型”。
# ─────────────────────────────────────────────────────────────────────────────
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "key_env": "",  # 本地服务通常不校验 key
    },
    "mock": {"base_url": "", "model": "extractive-mock", "key_env": ""},
}


def _http_post_json(url: str, payload: dict[str, Any], *, api_key: str = "",
                    timeout: int = 60) -> dict[str, Any]:
    """最小 HTTP JSON 客户端；不引入 requests，减少依赖面。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mock_answer(messages: list[dict[str, str]]) -> str:
    """
    离线演示用：不调用任何模型，直接摘录检索到的第一段原文。
    它证明链路（检索 → 提示词 → 生成接口）通了，但不具备真实的语言能力。
    """
    user = messages[-1]["content"] if messages else ""
    match = re.search(r"\[1\] 标题：(.+?)\n切片：([^\n]+)\n内容：(.+)", user, re.S)
    if not match:
        return "资料中没有相关规定。"
    heading, _, body = match.groups()
    # 取前两个完整句子，避免把整段 500 字照抄
    sentences = re.split(r"(?<=[。！？])", body.strip())
    excerpt = "".join(sentences[:2]).strip()
    return (
        "（离线 mock 模式：未调用真实模型，以下为资料原文摘录）\n"
        f"{excerpt} [1]\n"
        f"依据：{heading}"
    )


def call_llm(
    messages: list[dict[str, str]],
    *,
    provider: str = "mock",
    model: str = "",
    temperature: float = 0.2,
    max_tokens: int = 800,
    timeout: int = 60,
) -> LLMResult:
    """
    唯一的 LLM 出口。阶段 2 的 /chat 接口只调用它，不直接碰任何 SDK。

    provider : openai | deepseek | ollama | mock（也接受任意 OpenAI 兼容端点，
               通过 --base-url 覆盖）
    """
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        raise LLMError(f"未知 provider: {provider}（可选：{', '.join(PROVIDER_PRESETS)}）")

    resolved_model = model or os.environ.get("LLM_MODEL", "") or preset["model"]
    started = time.time()

    # -- mock：本地摘录，零网络 -------------------------------------------
    if provider == "mock":
        text = _mock_answer(messages)
        return LLMResult(
            text=text,
            provider=provider,
            model=resolved_model,
            usage={"prompt_tokens": sum(len(m["content"]) for m in messages) // 2,
                   "completion_tokens": len(text) // 2},
            elapsed_ms=int((time.time() - started) * 1000),
        )

    # -- 真实供应商：OpenAI 兼容协议 --------------------------------------
    api_key = os.environ.get(preset["key_env"], "").strip() if preset["key_env"] else ""
    if preset["key_env"] and not api_key:
        raise LLMError(
            f"provider={provider} 需要环境变量 {preset['key_env']}；"
            "可先用 --provider mock 验证链路"
        )

    base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/") or preset["base_url"]
    payload = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    # 失败重试一次：仅针对限流与服务端 5xx，网络错误直接抛出，避免静默拖长
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            data = _http_post_json(
                f"{base_url}/chat/completions", payload, api_key=api_key, timeout=timeout
            )
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last_error = LLMError(f"LLM 请求失败：HTTP {exc.code} {detail}")
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
            raise last_error from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM 网络错误：{exc.reason}") from exc
    else:  # for 循环未 break
        raise last_error or LLMError("LLM 调用失败")

    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as exc:
        raise LLMError(f"LLM 返回结构异常：{str(data)[:300]}") from exc

    return LLMResult(
        text=text,
        provider=provider,
        model=resolved_model,
        usage=data.get("usage", {}),
        elapsed_ms=int((time.time() - started) * 1000),
        raw=None if data.get("choices") else data,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. 索引构建 / 加载
#    [原型专用]（阶段 2 会把它变成 ingestion worker，但校验逻辑不变）
#    索引有效性由 meta 里的四项决定：制度文件哈希、切片长度、重叠、embedding 配置。
#    任何一项变了就重建，避免“文件改了但还在检索旧切片”。
# ─────────────────────────────────────────────────────────────────────────────
def policy_fingerprint(path: Path, size: int, overlap: int, embedder: Embedder) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "policy": path.name,
        "policy_sha256": hashlib.sha256(raw).hexdigest(),
        "chunk_size": size,
        "overlap": overlap,
        "embedding": embedder.provider,
        "embedding_model": embedder.model,
        "dim": embedder.dim,
    }


def ensure_index(
    policy: Path,
    index_dir: Path,
    *,
    size: int,
    overlap: int,
    embedder: Embedder,
    rebuild: bool = False,
) -> tuple[VectorStore, list[Chunk], BM25, dict[str, Any], bool]:
    """返回 (store, chunks, bm25, meta, rebuilt)。索引可用时直接加载。"""
    fingerprint = policy_fingerprint(policy, size, overlap, embedder)

    if not rebuild and (index_dir / "meta.json").exists():
        try:
            store, meta = VectorStore.load(index_dir)
            if all(meta.get(k) == v for k, v in fingerprint.items()):
                chunks = store.chunks
                bm25 = BM25([tokenize(c.text) for c in chunks])
                return store, chunks, bm25, meta, False
            print(f"[索引] 制度或参数已变化，重建（{index_dir}）")
        except (OSError, ValueError, KeyError) as exc:
            print(f"[索引] 读取失败（{exc}），重建")

    text = policy.read_text(encoding="utf-8")
    chunks = chunk_text(text, doc=policy.name, size=size, overlap=overlap)
    store = VectorStore.build(chunks, embedder)
    bm25 = BM25([tokenize(c.text) for c in chunks])
    meta = {**fingerprint, "chunk_count": len(chunks), "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    store.save(index_dir, meta)
    return store, chunks, bm25, meta, True


# ─────────────────────────────────────────────────────────────────────────────
# 10. 端到端问答
#     [核心·阶段2复用]（服务端 /chat 的内部实现就是这 30 行）
#     检索分低于阈值时直接拒答，不把“勉强相关”的段落交给模型去发挥。
# ─────────────────────────────────────────────────────────────────────────────
NO_EVIDENCE = "资料中没有相关规定。请补充问题背景，或联系财务部确认。"


def query_coverage(question: str, chunk: Chunk) -> float:
    """
    问题词项在片段中的覆盖率（只统计双字词）。

    为什么需要它：min-max 归一化后，排名第一的片段融合分总接近 1.0，
    单看融合分无法判断“到底有没有依据”。覆盖率回答的是另一个问题：
    问题里的关键概念，这个片段到底提到了多少。
    """
    tokens = {t for t in tokenize(question) if len(t) >= 2}
    if not tokens:
        return 0.0
    covered = sum(1 for t in tokens if t in chunk.text)
    return covered / len(tokens)


def answer_question(
    question: str,
    *,
    store: VectorStore,
    chunks: list[Chunk],
    bm25: BM25,
    embedder: Embedder,
    provider: str = "mock",
    model: str = "",
    top_k: int = 3,
    alpha: float = 0.5,
    min_score: float = 0.12,
    min_coverage: float = 0.2,
    temperature: float = 0.2,
) -> dict[str, Any]:
    hits = hybrid_search(
        question,
        chunks=chunks,
        bm25=bm25,
        store=store,
        embedder=embedder,
        top_k=top_k,
        alpha=alpha,
    )

    coverage = query_coverage(question, hits[0].chunk) if hits else 0.0
    if not hits or hits[0].score < min_score or coverage < min_coverage:
        return {
            "question": question,
            "answer": NO_EVIDENCE,
            "hits": [h.to_dict() for h in hits],
            "refused": True,
            "coverage": round(coverage, 3),
            "llm": None,
            "messages": [],
        }

    messages = build_messages(question, hits)
    llm = call_llm(messages, provider=provider, model=model, temperature=temperature)
    return {
        "question": question,
        "answer": llm.text,
        "hits": [h.to_dict() for h in hits],
        "refused": False,
        "coverage": round(coverage, 3),
        "llm": llm.to_dict(),
        "messages": messages,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 11. 离线自检
#     [原型专用]
#     断言覆盖：切片长度/重叠、BM25 排序、哈希向量稳定性、索引存取、
#     混合检索命中、提示词编号、mock 回答、无依据拒答。不依赖网络。
# ─────────────────────────────────────────────────────────────────────────────
def _selftest(policy: Path) -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        checks.append((name, bool(condition)))
        print(f"  {'PASS' if condition else 'FAIL'}  {name}")

    print("== 1. 切片 ==")
    text = policy.read_text(encoding="utf-8")
    chunks = chunk_text(text, doc=policy.name, size=500, overlap=80)
    check("切片数量 >= 5", len(chunks) >= 5)
    check("单片不超过 500 字", all(len(c.text) <= 500 for c in chunks))
    check("切片按原文顺序", all(chunks[i + 1].start >= chunks[i].end for i in range(len(chunks) - 1)))
    long_text = "# 长节\n" + ("这是一条用于验证重叠的测试制度内容。" * 60)
    long_chunks = chunk_text(long_text, size=500, overlap=80)
    check("长节内相邻切片重叠", len(long_chunks) >= 2 and long_chunks[1].start < long_chunks[0].end)
    check("切片不跨节", all(c.heading == "长节" for c in long_chunks))
    check("切片带标题", all(c.heading for c in chunks))
    check("chunk_id 唯一", len({c.chunk_id for c in chunks}) == len(chunks))

    print("== 2. BM25 ==")
    bm25 = BM25([tokenize(c.text) for c in chunks])
    scores = bm25.scores(tokenize("住宿标准"))
    top = int(np.argmax(scores))
    check("BM25 命中住宿条款", "住宿" in chunks[top].text)
    check("BM25 分数非全零", float(scores.max()) > 0)

    print("== 3. 向量 ==")
    emb = Embedder("hash")
    vec = emb.embed_query("住宿标准")
    check("向量维度正确", vec.shape == (EMBED_DIM,))
    check("向量已归一化", abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5)
    check("同文本向量稳定", np.allclose(vec, emb.embed_query("住宿标准")))

    print("== 4. 索引 ==")
    store = VectorStore.build(chunks, emb)
    check(f"后端可用（{store.backend}）", store.backend in {"faiss", "numpy"})
    pairs = store.search(vec, 3)
    check("向量检索返回 3 条", len(pairs) == 3)
    check("向量 top1 与住宿相关", "住宿" in chunks[pairs[0][0]].text)

    print("== 5. 混合检索 ==")
    hits = hybrid_search("出差住宿费上限是多少", chunks=chunks, bm25=bm25, store=store,
                         embedder=emb, top_k=3)
    check("混合检索返回 3 条", len(hits) == 3)
    check("top1 含住宿/650", ("住宿" in hits[0].chunk.text) or ("650" in hits[0].chunk.text))
    check("分数降序", all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1)))

    print("== 6. 提示词 ==")
    messages = build_messages("出差住宿费上限是多少", hits)
    joined = messages[0]["content"] + messages[1]["content"]
    check("提示词含资料编号 [1]", "[1]" in joined)
    check("提示词含问题", "出差住宿费上限是多少" in joined)
    check("系统提示要求拒答", "资料中没有相关规定" in messages[0]["content"])

    print("== 7. LLM 接口 ==")
    llm = call_llm(messages, provider="mock")
    check("mock 返回非空", bool(llm.text.strip()))
    check("mock 明确标注未调用模型", "mock" in llm.text)

    print("== 8. 无依据拒答 ==")
    refused = answer_question("公司年会在哪里举办", store=store, chunks=chunks, bm25=bm25,
                              embedder=emb, provider="mock")
    check("无依据时拒答", refused["refused"] is True)
    check("拒答文案统一", refused["answer"] == NO_EVIDENCE)
    check("拒答时覆盖率低", refused["coverage"] < 0.2)

    print("== 9. 索引持久化 ==")
    import shutil  # 仅自检使用；不用 tempfile，避免受限环境的 ACL 问题

    tmp_path = Path(__file__).resolve().parent / ".selftest-index"
    shutil.rmtree(tmp_path, ignore_errors=True)
    try:
        store.save(tmp_path, {"policy": policy.name})
        loaded, meta = VectorStore.load(tmp_path)
        check("重新加载切片数一致", len(loaded.chunks) == len(chunks))
        check("重新加载向量一致", np.allclose(loaded.vectors, store.vectors))
        check("meta 记录后端", meta.get("backend") in {"faiss", "numpy"})
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

    failed = [name for name, ok in checks if not ok]
    print()
    print(f"自检结果：{len(checks) - len(failed)}/{len(checks)} 通过")
    if failed:
        print("失败项：" + "；".join(failed))
        return 1
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# 12. 命令行入口
#     [原型专用]
#     参数全部可覆盖，但默认值就是“500 字切片 + top3 + mock LLM”，
#     保证新同学 clone 后一条命令即可看到完整链路。
# ─────────────────────────────────────────────────────────────────────────────
def _print_result(result: dict[str, Any], *, show_prompt: bool) -> None:
    print(f"\n问题：{result['question']}")
    print("\n检索（BM25 + 向量，top3）：")
    for i, hit in enumerate(result["hits"], start=1):
        print(f"  [{i}] 融合 {hit['score']:.3f}（BM25 {hit['bm25']:.3f} / 向量 {hit['vector']:.3f}）"
              f"  {hit['heading']}")
        print(f"      {hit['preview'][:80]}")
    print("\n回答：")
    print(result["answer"])
    if show_prompt and result.get("messages"):
        print("\n===== 实际发送的提示词 =====")
        for message in result["messages"]:
            print(f"\n## {message['role']}\n{message['content']}")
    if result.get("llm"):
        llm = result["llm"]
        print(f"\n[LLM] provider={llm['provider']} model={llm['model']} "
              f"{llm['elapsed_ms']}ms tokens={llm['usage']}")


def main(argv: Sequence[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="报销制度裸问答原型（500 字切片 + BM25/向量混合检索 + 可配置 LLM）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-q", "--question", help="要问的问题；配合 --selftest 时可省略")
    parser.add_argument("--policy", type=Path, default=here / "data" / "报销制度.md",
                        help="制度 Markdown 文件（默认 data/报销制度.md）")
    parser.add_argument("--index-dir", type=Path, default=here / ".index",
                        help="索引目录（默认 .index/，已 gitignore）")
    parser.add_argument("--rebuild", action="store_true", help="强制重建索引")
    parser.add_argument("--chunk-size", type=int, default=500, help="切片长度，默认 500")
    parser.add_argument("--overlap", type=int, default=80, help="重叠字符，默认 80")
    parser.add_argument("--top-k", type=int, default=3, help="返回条数，默认 3")
    parser.add_argument("--alpha", type=float, default=0.5, help="向量权重，默认 0.5")
    parser.add_argument("--min-score", type=float, default=0.12,
                        help="最低融合分，低于则拒答，默认 0.12")
    parser.add_argument("--provider", default=os.environ.get("LLM_PROVIDER", "mock"),
                        choices=sorted(PROVIDER_PRESETS), help="LLM 供应商，默认 mock")
    parser.add_argument("--model", default="", help="覆盖默认模型名")
    parser.add_argument("--embedding", default="hash", choices=["hash", "openai"],
                        help="embedding 方式，默认 hash（离线）")
    parser.add_argument("--embedding-model", default="", help="openai embedding 模型名")
    parser.add_argument("--show-prompt", action="store_true", help="打印实际发送的提示词")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于管道处理")
    parser.add_argument("--selftest", action="store_true", help="运行离线自检后退出")
    parser.add_argument("--version", action="version", version="rag_qa 1.0 (阶段 1 原型)")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest(args.policy)
    if not args.question:
        parser.error("需要 --question（或使用 --selftest）")
    if not args.policy.exists():
        sys.stderr.write(f"找不到制度文件：{args.policy}\n")
        return 2

    embedder = Embedder(args.embedding, model=args.embedding_model)
    if not HAS_FAISS:
        print("[提示] 未安装 faiss，使用 numpy 精确检索；安装 faiss-cpu 后自动切换。")

    store, chunks, bm25, meta, rebuilt = ensure_index(
        args.policy,
        args.index_dir,
        size=args.chunk_size,
        overlap=args.overlap,
        embedder=embedder,
        rebuild=args.rebuild,
    )
    print(f"[索引] {args.index_dir}｜切片 {len(chunks)}｜后端 {store.backend}｜"
          f"{'新建' if rebuilt else '复用'}")

    result = answer_question(
        args.question,
        store=store,
        chunks=chunks,
        bm25=bm25,
        embedder=embedder,
        provider=args.provider,
        model=args.model,
        top_k=args.top_k,
        alpha=args.alpha,
        min_score=args.min_score,
    )

    if args.json:
        result.pop("messages", None)  # 提示词可能很长，默认不塞进 JSON
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_result(result, show_prompt=args.show_prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
