"""聊天与账户 API：JWT、SSE、会话、反馈、工单上下文。"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..utils.errors import TraceError
from .agent import Agent
from .pages import esc
from .security import sign, verify

router = APIRouter(prefix="/api/v1")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="", max_length=64)


class FeedbackIn(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="未确认", max_length=16)
    note: str = Field(default="", max_length=500)


def _store(request: Request):
    return request.app.state.store


def _settings(request: Request):
    return request.app.state.settings


def _dataset(request: Request):
    return request.app.state.dataset


def user_from(request: Request) -> dict[str, Any]:
    token = ""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    token = token or request.cookies.get("expo_token", "")
    if not token:
        raise HTTPException(401, detail={"code": "E_AUTH_REQUIRED", "message": "请先登录"})
    try:
        payload = verify(token, _settings(request).secret)
    except TraceError as exc:
        raise HTTPException(401, detail={"code": "E_AUTH_REQUIRED", "message": str(exc)}) from exc
    row = _store(request).user_by_id(payload.get("sub", ""))
    if not row or row["disabled"]:
        raise HTTPException(401, detail={"code": "E_AUTH_REQUIRED", "message": "账号不可用"})
    return {"id": row["id"], "username": row["username"], "name": row["name"],
            "role": row["role"], "jti": payload.get("jti", "")}


def require(request: Request, roles: tuple[str, ...]) -> dict[str, Any]:
    user = user_from(request)
    if user["role"] not in roles:
        raise HTTPException(403, detail={"code": "E_FORBIDDEN", "message": "没有该操作的权限"})
    return user


def _sse(event: dict[str, Any]) -> str:
    name = event.get("type", "message")
    data = {k: v for k, v in event.items() if k != "type"}
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    run_dir = _dataset(request).run_dir
    return {"status": "ok", "paused": (run_dir / "paused.json").exists(),
            "run_dir": str(run_dir), "llm_provider": settings.llm_provider,
            "embedder": settings.embedder, "ts": int(time.time())}


@router.post("/auth/login")
def login(request: Request, body: LoginIn) -> JSONResponse:
    store = _store(request)
    row = store.authenticate(body.username, body.password)
    if not row:
        store.audit("login_failed", body.username, "auth", {"username": body.username})
        raise HTTPException(401, detail={"code": "E_AUTH_FAILED", "message": "账号或密码不正确"})
    token = sign({"sub": row["id"], "role": row["role"], "jti": __import__("uuid").uuid4().hex},
                 _settings(request).secret)
    store.audit("login", row["username"], "auth", {"role": row["role"]})
    resp = JSONResponse({"token": token, "user": {"username": row["username"], "name": row["name"],
                                                  "role": row["role"]}})
    resp.set_cookie("expo_token", token, httponly=True, samesite="lax", max_age=720 * 3600)
    return resp


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    user = user_from(request)
    return {"username": user["username"], "name": user["name"], "role": user["role"]}


@router.post("/sessions")
def create_session(request: Request) -> dict[str, Any]:
    user = user_from(request)
    sid = _store(request).create_session(user["id"], "")
    return {"session_id": sid}


@router.get("/sessions")
def list_sessions(request: Request) -> dict[str, Any]:
    user = user_from(request)
    with _store(request).conn() as db:
        rows = db.execute(
            "SELECT id,title,created_at,updated_at FROM sessions WHERE user_id=?"
            " ORDER BY updated_at DESC LIMIT 20", (user["id"],)).fetchall()
    return {"sessions": [dict(r) for r in rows]}


@router.get("/sessions/{sid}/messages")
def session_messages(request: Request, sid: str) -> dict[str, Any]:
    user = user_from(request)
    store = _store(request)
    session = store.session(sid)
    if not session or session["user_id"] != user["id"]:
        raise HTTPException(404, detail={"code": "E_NOT_FOUND", "message": "会话不存在"})
    out = []
    for row in store.history(sid, limit=40):
        out.append({"id": row["id"], "role": row["role"], "content": row["content"],
                    "answer_type": row["answer_type"], "blocked": bool(row["blocked"]),
                    "created_at": row["created_at"],
                    "citations": [dict(c) for c in store.citations(row["id"])]})
    return {"messages": out}


@router.post("/chat")
def chat(request: Request, body: ChatIn) -> StreamingResponse:
    user = user_from(request)
    store = _store(request)
    sid = body.session_id
    if not sid:
        sid = store.create_session(user["id"], body.question[:60])
    else:
        session = store.session(sid)
        if not session or session["user_id"] != user["id"]:
            raise HTTPException(404, detail={"code": "E_NOT_FOUND", "message": "会话不存在"})
    store.add_message(sid, "user", body.question)
    store.touch_session(sid, body.question[:60])
    store.audit("chat_asked", user["username"], sid, {"chars": len(body.question)})

    agent = Agent(store, _dataset(request), _settings(request), user=user, session_id=sid)

    def generator() -> Iterator[str]:
        yield f"retry: 3000\n\nevent: meta\ndata: {json.dumps({'session_id': sid}, ensure_ascii=False)}\n\n"
        try:
            for event in agent.stream(body.question):
                if event.get("type") == "delta":
                    yield f"event: delta\ndata: {json.dumps({'text': event['text']}, ensure_ascii=False)}\n\n"
                else:
                    yield _sse(event)
        except Exception as exc:  # 生产应记录堆栈；这里只回传可读错误，不泄漏内部路径
            store.audit("chat_failed", user["username"], sid, {"error": type(exc).__name__})
            payload = {"code": "E_INTERNAL", "message": "服务端处理失败，请稍后重试或转人工"}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/feedback")
def feedback(request: Request, body: FeedbackIn) -> dict[str, Any]:
    user = user_from(request)
    store = _store(request)
    with store.conn() as db:
        row = db.execute(
            "SELECT m.id FROM messages m JOIN sessions s ON s.id=m.session_id"
            " WHERE m.id=? AND s.user_id=?", (body.message_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, detail={"code": "E_NOT_FOUND", "message": "消息不存在"})
    fid = __import__("uuid").uuid4().hex
    with store.conn() as db:
        db.execute("INSERT INTO feedback(id,message_id,kind,reporter,note,created_at)"
                   " VALUES(?,?,?,?,?,datetime('now'))",
                   (fid, body.message_id, body.kind, user["username"], body.note))
    store.audit("feedback", user["username"], body.message_id, {"kind": body.kind})
    return {"feedback_id": fid}


@router.get("/tickets/{ticket_id}/context")
def ticket_context(request: Request, ticket_id: str) -> dict[str, Any]:
    user_from(request)
    dataset = _dataset(request)
    ticket = dataset.ticket(ticket_id.upper())
    if not ticket:
        raise HTTPException(404, detail={"code": "E_NOT_FOUND", "message": "工单不存在"})
    res = dataset.resolution(ticket_id.upper()) or {}
    version = dataset.current(ticket_id.upper())
    return {"ticket": {"ticket_id": ticket["ticket_id"], "batch": ticket["batch"],
                       "category": ticket["category"], "question": ticket["question"],
                       "assignee": ticket["assignee"], "occurred": ticket.get("occurred"),
                       "received": ticket.get("received")},
            "resolution": {"state": res.get("state"), "version_id": res.get("version_id"),
                           "reason": esc(res.get("reason", "")),
                           "reason_code": res.get("reason_code")},
            "version": None if not version else {
                "version_id": version["version_id"], "notice_id": version["notice_id"],
                "effective_from": version["effective_from"],
                "effective_to": version["effective_to"],
                "confirmed_by": version["confirmed_by"], "statement": version["statement"]},
            "unconfirmed_items": dataset.unconfirmed_items(ticket_id.upper())}
