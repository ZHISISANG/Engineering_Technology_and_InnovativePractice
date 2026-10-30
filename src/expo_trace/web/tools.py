"""Agent 工具层：Function Calling 的白名单与顺序约束都在这里。

关键设计
--------
1. 工具表里没有 send / publish / approve / write_back —— 边界靠“不存在”保证，
   而不是靠提示词劝阻。
2. 顺序约束在服务端强制：compose_draft 需要本轮的 check_fields 令牌，
   request_human_review 又需要 compose_draft 的产物。模型跳步只会拿到
   E_ORDER_VIOLATION，而不是一张已提交的单。
3. 令牌是 HMAC 签名 + 数据库一次性 jti，绑定 工单/版本/操作人，
   10 分钟过期；签发后版本被作废，再调用得到 E_VERSION_STALE。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from ..utils.errors import TraceError
from ..utils.timeutil import LOCAL_TZ, iso
from .dataset import Dataset
from .settings import Settings
from .store import Store

TOOL_NAMES = (
    "get_ticket", "search_versions", "resolve_version", "check_fields", "search_policy",
    "get_handoff", "compose_draft", "request_human_review", "record_feedback", "get_pilot_status",
)

READ_ONLY = {"get_ticket", "search_versions", "resolve_version", "check_fields",
             "search_policy", "get_handoff", "get_pilot_status"}

TOKEN_TTL_SECONDS = 600
WATERMARK = "【未确认 · 不可对外】"


class ToolError(TraceError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Tools:
    def __init__(self, store: Store, dataset: Dataset, settings: Settings, actor: str,
                 session_id: str = "") -> None:
        self.store = store
        self.dataset = dataset
        self.settings = settings
        self.actor = actor
        self.session_id = session_id
        self.calls: list[dict[str, Any]] = []

    # -- 入口 -------------------------------------------------------------
    def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        if name not in TOOL_NAMES:
            raise ToolError("E_UNKNOWN_TOOL", f"工具不存在：{name}")
        handler = getattr(self, f"_tool_{name}")
        try:
            result = handler(**kwargs)
            ok, code = True, ""
        except ToolError as exc:
            ok, code = False, exc.code
            result = {"error": exc.code, "message": str(exc)}
        self.calls.append({"name": name, "args": kwargs, "ok": ok, "code": code})
        self.store.audit("tool_called", self.actor, kwargs.get("ticket_id", "") or name,
                         {"tool": name, "ok": ok, "code": code, "session_id": self.session_id})
        if not ok:
            raise ToolError(result["error"], result["message"])
        return result

    def _require_prior(self, ticket_id: str, name: str, *, version_id: str = "") -> None:
        for call in reversed(self.calls):
            if call["name"] != name or not call["ok"]:
                continue
            if call["args"].get("ticket_id") != ticket_id:
                continue
            if version_id and call["args"].get("_version_id") not in ("", version_id):
                continue
            return
        raise ToolError("E_ORDER_VIOLATION", f"必须先调用 {name}（同一工单）再调用当前工具")

    # -- 只读工具 ---------------------------------------------------------
    def _tool_get_ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.dataset.ticket(ticket_id)
        if not ticket:
            raise ToolError("E_NOT_FOUND", f"工单不存在：{ticket_id}")
        return {
            "ticket_id": ticket_id, "batch": ticket["batch"], "category": ticket["category"],
            "question": ticket["question"], "assignee": ticket["assignee"],
            "occurred_at": (ticket.get("occurred") or {}).get("value", ""),
            "received_at": (ticket.get("received") or {}).get("value", ""),
            "occurred_state": (ticket.get("occurred") or {}).get("state", ""),
            "received_state": (ticket.get("received") or {}).get("state", ""),
        }

    def _tool_search_versions(self, batch: str, category: str, moment: str = "") -> dict[str, Any]:
        rows = self.dataset.search_versions(batch, category, moment)
        return {"versions": [{"version_id": v["version_id"], "notice_id": v["notice_id"],
                              "effective_from": v["effective_from"],
                              "effective_to": v["effective_to"],
                              "confirmed": v["confirmed"], "void": v["void"]} for v in rows[:10]]}

    def _tool_resolve_version(self, ticket_id: str) -> dict[str, Any]:
        res = self.dataset.resolution(ticket_id)
        if not res:
            raise ToolError("E_NOT_FOUND", f"缺少工单判定结果：{ticket_id}")
        return {"ticket_id": ticket_id, "state": res["state"], "version_id": res["version_id"],
                "reason": res["reason"], "reason_code": res["reason_code"],
                "chain_break": res["chain_break"], "considered": res["considered"][:6]}

    def _tool_check_fields(self, ticket_id: str, version_id: str = "") -> dict[str, Any]:
        ticket = self.dataset.ticket(ticket_id)
        res = self.dataset.resolution(ticket_id)
        if not ticket or not res:
            raise ToolError("E_NOT_FOUND", f"工单不存在：{ticket_id}")
        missing: list[str] = []
        if (ticket.get("occurred") or {}).get("state") != "ok":
            missing.append("业务发生时间")
        if (ticket.get("received") or {}).get("state") != "ok":
            missing.append("系统接收时间")
        if not ticket.get("category"):
            missing.append("问题类别")
        if not ticket.get("assignee"):
            missing.append("处理人")
        if res.get("state") != "current":
            missing.append(f"当前版本（判定：{res.get('state')} / {res.get('reason_code') or '-'}）")
        if version_id and res.get("version_id") and version_id != res["version_id"]:
            raise ToolError("E_VERSION_STALE", "指定版本与判定结果不一致，请重新判定")
        if missing:
            return {"ok": False, "missing": missing, "ticket_id": ticket_id}
        token = self._issue_token(ticket_id, res["version_id"])
        return {"ok": True, "missing": [], "ticket_id": ticket_id,
                "version_id": res["version_id"], "context_token": token,
                "expires_in": TOKEN_TTL_SECONDS}

    def _tool_search_policy(self, query: str, top_k: int = 3, scope: str = "",
                            moment: str = "") -> dict[str, Any]:
        from .kb import search
        hits = search(self.store, query, top_k=top_k, scope=scope, moment=moment,
                      provider=self.settings.embedder)
        return {"hits": [{"chunk_id": h.chunk_id, "doc_id": h.doc_id, "title": h.title,
                          "heading": h.heading, "text": h.text, "score": h.score,
                          "version_label": h.version_label} for h in hits]}

    def _tool_get_handoff(self, ticket_id: str) -> dict[str, Any]:
        row = self.dataset.handoff(ticket_id)
        if not row:
            raise ToolError("E_NOT_FOUND", f"该工单没有交接记录：{ticket_id}")
        return {"handoff": {k: v for k, v in row.items() if v not in ("", None)}}

    def _tool_get_pilot_status(self) -> dict[str, Any]:
        manifest = {}
        path = self.dataset.run_dir / "manifest.json"
        if path.exists():
            manifest = json.loads(path.read_text(encoding="utf-8"))
        paused_file = self.dataset.run_dir / "paused.json"
        # 约定：paused.json 存在即暂停（与 RunStore.pause/resume 一致），
        # 不依赖文件里的字段；读不出来也按暂停处理，宁可停下。
        paused = paused_file.exists()
        if paused:
            try:
                json.loads(paused_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                paused = True
        return {"paused": paused,
                "run_dir": str(self.dataset.run_dir),
                "counts": manifest.get("counts", {}),
                "degraded": manifest.get("degraded", False)}

    # -- 写工具（受顺序约束）---------------------------------------------
    def _tool_compose_draft(self, ticket_id: str, context_token: str) -> dict[str, Any]:
        self._require_prior(ticket_id, "check_fields")
        payload = self._verify_token(context_token, ticket_id)
        version = self.dataset.version(payload["version_id"])
        ticket = self.dataset.ticket(ticket_id) or {}
        if not version:
            raise ToolError("E_VERSION_STALE", "依据版本已不存在")
        if version.get("void"):
            raise ToolError("E_VERSION_STALE", "依据版本已作废")
        lines = [
            WATERMARK,
            f"工单：{ticket_id}｜批次 {ticket.get('batch', '')}｜类别 {ticket.get('category', '')}",
            f"问题：{ticket.get('question', '')}",
            f"处理人：{ticket.get('assignee', '')}",
            f"业务发生：{(ticket.get('occurred') or {}).get('value', '')}",
            f"系统接收：{(ticket.get('received') or {}).get('value', '')}",
            "",
            f"依据版本：{version['version_id']}（通知 {version['notice_id']}）",
            f"有效期：{version['effective_from'][:10]} ~ {(version['effective_to'] or '长期')[:10]}",
            f"确认人：{version['confirmed_by']}（{version['confirmed_at'][:10]}）",
            f"规则原文：{version['statement']}",
            "",
            "未确认事项：",
        ]
        items = self.dataset.unconfirmed_items(ticket_id)
        lines += [f"- {item}" for item in items] or ["- 无（仍需审核方确认口径适用性）"]
        lines += ["", "处理建议：一线核对后，由审核方确认再对外发送；本工具不发送、不写回源系统。"]
        body = "\n".join(lines)
        draft_id = self.store.draft_add(ticket_id, version["version_id"], body, self.actor)
        return {"draft_id": draft_id, "body": body, "version_id": version["version_id"]}

    def _tool_request_human_review(self, ticket_id: str, draft_id: str,
                                   context_token: str) -> dict[str, Any]:
        self._require_prior(ticket_id, "compose_draft")
        payload = self._verify_token(context_token, ticket_id)
        draft = self.store.draft(draft_id)
        if not draft or draft["ticket_id"] != ticket_id:
            raise ToolError("E_ORDER_VIOLATION", "草稿不存在或不属于该工单")
        if draft["version_id"] != payload["version_id"]:
            raise ToolError("E_VERSION_STALE", "草稿依据版本与令牌不一致")
        review_id = self.store.review_add(draft_id, ticket_id, self.actor)
        self.store.token_consume(payload["jti"])
        return {"review_id": review_id, "ticket_id": ticket_id, "status": "queued"}

    def _tool_record_feedback(self, ticket_id: str, kind: str, note: str) -> dict[str, Any]:
        from ..core.feedback import KINDS
        if kind not in KINDS:
            raise ToolError("E_INVALID_ARGUMENT", f"不支持的反馈类型：{kind}")
        fid = uuid.uuid4().hex
        with self.store.conn() as db:
            db.execute(
                "INSERT INTO feedback(id,message_id,kind,reporter,note,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (fid, "", kind, self.actor, note, iso(datetime.now(tz=LOCAL_TZ)) or ""),
            )
        return {"feedback_id": fid, "kind": kind}

    # -- 令牌 -------------------------------------------------------------
    def _sign(self, payload: dict[str, Any]) -> str:
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        mac = hmac.new(self.settings.secret.encode(), body.encode(), hashlib.sha256).digest()
        return f"{body}.{_b64(mac)}"

    def _issue_token(self, ticket_id: str, version_id: str) -> str:
        jti = uuid.uuid4().hex
        expires = datetime.now(tz=LOCAL_TZ) + timedelta(seconds=TOKEN_TTL_SECONDS)
        self.store.token_issue(jti, ticket_id, version_id, self.actor, iso(expires) or "")
        return self._sign({"jti": jti, "ticket_id": ticket_id, "version_id": version_id,
                           "actor": self.actor, "exp": int(expires.timestamp())})

    def _verify_token(self, token: str, ticket_id: str) -> dict[str, Any]:
        try:
            body, mac = token.split(".")
        except ValueError as exc:
            raise ToolError("E_ORDER_VIOLATION", "缺少有效的上下文令牌") from exc
        want = hmac.new(self.settings.secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(mac)):
            raise ToolError("E_ORDER_VIOLATION", "上下文令牌签名无效")
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            raise ToolError("E_CONTEXT_EXPIRED", "上下文令牌已过期，请重新校验字段")
        if payload.get("ticket_id") != ticket_id:
            raise ToolError("E_ORDER_VIOLATION", "令牌与工单不匹配")
        row = self.store.token_get(payload["jti"])
        if not row:
            raise ToolError("E_ORDER_VIOLATION", "令牌不存在")
        if row["used_at"]:
            raise ToolError("E_CONTEXT_USED", "上下文令牌已被使用，请重新校验字段")
        current = self.dataset.current(ticket_id)
        if not current or current["version_id"] != payload["version_id"]:
            raise ToolError("E_VERSION_STALE", "版本已变化，请重新判定后再起草")
        return payload
