"""LLM 适配：只做“拼消息 + 调唯一出口”，不在这里写业务判断。"""

from __future__ import annotations

import time
from typing import Any, Sequence

from .. import rag_qa
from .kb import KbHit, render_context

FALLBACK_SYSTEM = (
    "你是展会咨询工单助手。只根据给定资料回答；资料没有就说“资料中没有相关规定”；"
    "涉及收费、费用、资格的问题不给对外结论；回答末尾列出引用编号。"
)


def build_messages(question: str, hits: Sequence[KbHit], system_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt or FALLBACK_SYSTEM},
        {"role": "user",
         "content": f"资料：\n{render_context(hits)}\n\n问题：{question}\n\n"
                    "要求：只使用上面的资料；每个结论后用 [编号] 标注依据；"
                    "资料不足时直接回答“资料中没有相关规定”。"},
    ]


def answer(question: str, hits: Sequence[KbHit], system_prompt: str, *,
           provider: str = "mock", model: str = "") -> dict[str, Any]:
    started = time.time()
    messages = build_messages(question, hits, system_prompt)
    result = rag_qa.call_llm(messages, provider=provider, model=model)
    return {
        "text": result.text.strip(),
        "provider": result.provider,
        "model": result.model,
        "latency_ms": int((time.time() - started) * 1000),
    }
