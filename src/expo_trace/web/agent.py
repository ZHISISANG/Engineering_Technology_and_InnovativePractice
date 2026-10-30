"""把一次提问变成决策序列：边界 → 工单/版本 → 检索 → 生成 → 留痕。

这里没有自由发挥的 ReAct：路由是确定性的，模型只在“资料已检索到”之后
负责组织语言。原因是这类工单问答最贵的错误是张冠李戴的版本与越权承诺，
而不是措辞不够漂亮。
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterator

from ..utils.timeutil import LOCAL_TZ, iso
from . import kb, llm
from .dataset import Dataset
from .settings import Settings
from .store import Store
from .tools import ToolError, Tools

TICKET_RE = re.compile(r"T-\d{4}", re.I)
BOUNDARY_WORDS = ("收费", "费用", "展位费", "参展费", "会务费", "服务费", "收费标准", "报价",
                  "折扣", "退款", "资格", "押金", "承诺", "减免", "免费")
DRAFT_WORDS = ("草稿", "起草", "回复", "怎么写", "拟一份", "拟稿")
MIN_EVIDENCE = 0.15


def _chunks(text: str, size: int = 26) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i:i + size]


class Agent:
    def __init__(self, store: Store, dataset: Dataset, settings: Settings, *,
                 user: dict[str, Any], session_id: str, sleep: float = 0.0) -> None:
        self.store = store
        self.dataset = dataset
        self.settings = settings
        self.user = user
        self.session_id = session_id
        self.sleep = sleep
        self.tools = Tools(store, dataset, settings, actor=user["username"], session_id=session_id)
        self.message_id = ""
        self.citations: list[dict[str, Any]] = []
        self._buffer: list[str] = []

    # -- 主流程 -----------------------------------------------------------
    def stream(self, question: str) -> Iterator[dict[str, Any]]:
        started = time.time()
        yield {"type": "status", "stage": "route", "text": "正在判断问题类型…"}
        ticket_id = self._ticket_id(question)
        lowered = question

        if self._paused():
            yield from self._finish("blocked", "E_PILOT_PAUSED",
                                    "试点已由审核方暂停，当前不生成任何草稿。请等待恢复通知。",
                                    started, blocked=True)
            return

        if any(word in lowered for word in BOUNDARY_WORDS) and not any(
                word in lowered for word in DRAFT_WORDS):
            text = ("这个问题涉及收费 / 资格口径，属于不可逆边界：系统不生成对外结论，"
                    "也不自动发布。请把问题转给审核方（顾明岚 / 傅景）人工确认后再答复。")
            if ticket_id:
                res = self.dataset.resolution(ticket_id)
                if res and res.get("version_id"):
                    version = self.dataset.version(res["version_id"])
                    if version:
                        self._cite_version(version)
                        text += f"\n\n可核对到的最新版本：{version['version_id']}（通知 {version['notice_id']}），仅供内部核对，不构成对外结论。"
            yield from self._finish("blocked", "E_BOUNDARY_BLOCKED", text, started, blocked=True)
            return

        if ticket_id:
            yield from self._answer_ticket(ticket_id, question, started)
        else:
            yield from self._answer_policy(question, started)

    # -- 工单路径 ---------------------------------------------------------
    def _answer_ticket(self, ticket_id: str, question: str, started: float) -> Iterator[dict[str, Any]]:
        yield {"type": "status", "stage": "ticket", "text": f"正在核对工单 {ticket_id} 的版本…"}
        try:
            self.tools.call("get_ticket", ticket_id=ticket_id)
            res = self.tools.call("resolve_version", ticket_id=ticket_id)
        except ToolError as exc:
            yield from self._finish("error", exc.code, str(exc), started)
            return

        wants_draft = any(word in question for word in DRAFT_WORDS)
        if res["state"] != "current":
            text = (f"工单 {ticket_id} 当前无法定位已确认的现行版本（判定：{res['state']}，"
                    f"原因码：{res['reason_code'] or '-'}）。\n"
                    f"原因：{res['reason']}\n\n已转人工队列，不生成回复草稿；"
                    "请流程协调员补齐字段或由审核方确认版本后再处理。")
            if res.get("version_id"):
                version = self.dataset.version(res["version_id"])
                if version:
                    self._cite_version(version)
            yield from self._finish("blocked", "E_VERSION_UNRESOLVED", text, started, blocked=True)
            return

        version = self.dataset.version(res["version_id"])
        if version:
            self._cite_version(version)

        if not wants_draft:
            ticket = self.dataset.ticket(ticket_id) or {}
            items = self.dataset.unconfirmed_items(ticket_id)
            text = (
                f"工单 {ticket_id} 可定位到现行版本 {version['version_id']}"
                f"（通知 {version['notice_id']}，有效期 {version['effective_from'][:10]} ~ "
                f"{(version['effective_to'] or '长期')[:10]}，确认人 {version['confirmed_by']}）。\n\n"
                f"规则原文：{version['statement']}\n\n"
                f"问题：{ticket.get('question', '')}\n"
                "如果你的问题不在上述原文覆盖范围内，请不要直接引用这句话回答展商。"
            )
            if items:
                text += "\n\n未确认事项：\n" + "\n".join(f"- {item}" for item in items)
            yield from self._finish("ok", "", text, started)
            return

        yield {"type": "status", "stage": "fields", "text": "正在校验关键字段…"}
        try:
            checked = self.tools.call("check_fields", ticket_id=ticket_id, version_id=version["version_id"])
        except ToolError as exc:
            yield from self._finish("error", exc.code, str(exc), started)
            return
        if not checked.get("ok"):
            text = (f"关键字段不完整（{'、'.join(checked['missing'])}），已转人工队列。\n"
                    "按规则不生成草稿：缺字段时生成的回复无法追溯。")
            yield from self._finish("blocked", "E_FIELDS_MISSING", text, started, blocked=True)
            return

        yield {"type": "status", "stage": "draft", "text": "正在生成草稿并申请复核…"}
        token = checked["context_token"]
        draft = self.tools.call("compose_draft", ticket_id=ticket_id, context_token=token)
        review = self.tools.call("request_human_review", ticket_id=ticket_id,
                                 draft_id=draft["draft_id"], context_token=token)
        text = draft["body"] + f"\n\n已提交人工复核：{review['review_id']}（状态 queued）。"
        yield from self._finish("ok", "", text, started, extra={"review_id": review["review_id"],
                                                                "draft_id": draft["draft_id"]})

    # -- 知识问答 ---------------------------------------------------------
    def _answer_policy(self, question: str, started: float) -> Iterator[dict[str, Any]]:
        yield {"type": "status", "stage": "retrieve", "text": "正在检索展会通知与规则…"}
        hits = kb.search(self.store, question, top_k=3, provider=self.settings.embedder)
        if not hits or hits[0].score < MIN_EVIDENCE:
            yield from self._finish(
                "refused", "E_NO_EVIDENCE",
                "资料中没有相关规定。为避免编造，系统不在无依据时作答；"
                "请补充问题或让流程协调员上传对应通知后重试。", started)
            return
        for hit in hits:
            self._cite_policy(hit)
        yield {"type": "status", "stage": "generate", "text": "正在生成回答…"}
        system_prompt = self.store.prompt_active()["content"]
        result = llm.answer(question, hits, system_prompt, provider=self.settings.llm_provider)
        for piece in _chunks(result["text"]):
            self._buffer.append(piece)
            yield {"type": "delta", "text": piece}
            if self.sleep:
                time.sleep(self.sleep)
        suffix = "\n\n引用：" + "；".join(
            f"[{i}] {h.title}·{h.heading}" for i, h in enumerate(hits, start=1))
        for piece in _chunks(suffix):
            self._buffer.append(piece)
            yield {"type": "delta", "text": piece}
        yield from self._finish("ok", "", "", started, streamed=True,
                                provider=result["provider"], model=result["model"])

    # -- 收尾 -------------------------------------------------------------
    def _finish(self, answer_type: str, code: str, text: str, started: float, *,
                blocked: bool = False, streamed: bool = False,
                provider: str = "", model: str = "",
                extra: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        if text and not streamed:
            for piece in _chunks(text):
                yield {"type": "delta", "text": piece}
                if self.sleep:
                    time.sleep(self.sleep)
            body = text
        elif streamed:
            body = "".join(self._buffer)
        else:
            body = ""
        elapsed = int((time.time() - started) * 1000)
        self.message_id = self.store.add_message(
            self.session_id, "assistant", body, answer_type=answer_type,
            error_code=code, blocked=blocked, provider=provider, model=model,
            latency_ms=elapsed)
        for cite in self.citations:
            cite["message_id"] = self.message_id
            self.store.add_citation(self.message_id, **{k: v for k, v in cite.items()
                                                        if k != "message_id"})
        for cite in self.citations:
            yield {"type": "citation", "citation": cite}
        self.store.audit("chat_answered", self.user["username"], self.session_id,
                         {"answer_type": answer_type, "code": code, "message_id": self.message_id,
                          "citations": len(self.citations)})
        payload = {"type": "done", "message_id": self.message_id, "answer_type": answer_type,
                   "code": code, "citations": len(self.citations)}
        payload.update(extra or {})
        yield payload

    def _cite_version(self, version: dict[str, Any]) -> None:
        self.citations.append({
            "kind": "ticket", "ref_id": version["version_id"], "version_id": version["version_id"],
            "title": f"{version['notice_id']}｜{version['version_id']}",
            "snippet": version["statement"],
            "effective_from": version["effective_from"], "effective_to": version["effective_to"] or "",
            "confirmed_by": version["confirmed_by"], "doc_id": "", "chunk_id": "",
        })

    def _cite_policy(self, hit: kb.KbHit) -> None:
        self.citations.append({
            "kind": "policy", "ref_id": hit.doc_id, "version_id": "", "title": f"{hit.title}·{hit.heading}",
            "snippet": hit.text, "effective_from": hit.effective_from,
            "effective_to": hit.effective_to, "confirmed_by": "", "doc_id": hit.doc_id,
            "chunk_id": hit.chunk_id,
        })

    def _paused(self) -> bool:
        return (self.dataset.run_dir / "paused.json").exists()

    @staticmethod
    def _ticket_id(text: str) -> str:
        match = TICKET_RE.search(text)
        return match.group(0).upper() if match else ""
