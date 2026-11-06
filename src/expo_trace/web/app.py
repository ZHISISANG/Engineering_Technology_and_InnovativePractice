"""FastAPI 应用装配：路由、静态资源、异常格式与安全响应头。

启动方式见 docs/phase2/chat-api.md：
    expo-trace web --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import admin, chat, pages
from .dataset import Dataset
from .settings import Settings
from .store import Store

log = logging.getLogger("expo_trace.web")

STATIC = Path(__file__).parent / "static"


def create_app(*, settings: Settings | None = None, store: Store | None = None,
               dataset: Dataset | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(settings.db_path)
    store.seed(settings.seed_password)
    dataset = dataset or Dataset(settings.run_dir)

    app = FastAPI(title="展会咨询工单助手", version="0.2.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.dataset = dataset

    app.include_router(chat.router)
    app.include_router(admin.router)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self';"
            " connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            payload = detail
        else:
            payload = {"code": "E_HTTP", "message": str(detail)}
        if request.url.path.startswith("/api/"):
            return JSONResponse(payload, status_code=exc.status_code)
        return JSONResponse(payload, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("未处理异常 path=%s", request.url.path)
        return JSONResponse({"code": "E_INTERNAL", "message": "服务端处理失败"},
                            status_code=500)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/chat", status_code=303)

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_form(request: Request) -> Any:
        """普通用户（咨询员）入口。"""
        try:
            user = chat.user_from(request)
        except HTTPException:
            return HTMLResponse(pages.login_page(kind="staff"))
        return RedirectResponse("/chat" if user["role"] == "agent" else "/admin", status_code=303)

    @app.post("/login", include_in_schema=False)
    def login_submit(request: Request, username: str = Form(...), password: str = Form(...)) -> Any:
        store = request.app.state.store
        row = store.authenticate(username, password)
        if not row:
            store.audit("login_failed", username, "auth", {"username": username, "via": "staff"})
            return HTMLResponse(pages.login_page("账号或密码不正确", kind="staff"), status_code=401)
        if row["role"] != "staff":   # 角色隔离：审核方/管理员走后台入口
            return HTMLResponse(pages.login_page("该账号是审核方/管理员，请使用后台入口登录", kind="staff"),
                                status_code=401)
        jti = uuid.uuid4().hex
        token = admin.sign({"sub": row["id"], "role": row["role"], "jti": jti},
                           request.app.state.settings.secret)
        store.audit("login", row["username"], "auth", {"role": row["role"], "via": "staff"})
        target = "/admin" if row["role"] in ("admin", "reviewer") else "/chat"
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie("expo_token", token, httponly=True, samesite="lax", max_age=720 * 3600)
        resp.set_cookie("expo_csrf", admin.csrf_token(request.app.state.settings.secret, jti),
                        samesite="lax", max_age=720 * 3600)
        return resp

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request) -> RedirectResponse:
        """退出后回到普通用户入口。"""
        try:
            user = chat.user_from(request)
            request.app.state.store.audit("logout", user["username"], "auth", {})
        except HTTPException:
            pass
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("expo_token")
        resp.delete_cookie("expo_csrf")
        return resp

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    def chat_page(request: Request) -> Any:
        try:
            user = chat.user_from(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "staff":   # 审核方/管理员不进咨询员界面
            return RedirectResponse("/admin", status_code=303)
        store = request.app.state.store
        sessions = store.sessions_for(user["id"])
        active = request.query_params.get("session", "")
        ids = {row["id"] for row in sessions}
        if active and active not in ids:      # 不允许看别人的会话
            active = ""
        if not active:
            if sessions:
                active = sessions[0]["id"]
            else:                              # 首次进入自动建一个空会话
                active = store.create_session(user["id"], "")
                sessions = store.sessions_for(user["id"])
        history = store.messages_for(active) if active else []
        drafts = store.drafts_for(user["username"])
        return HTMLResponse(pages.chat_page(user, sessions=sessions, active_id=active,
                                            history=history, drafts_count=len(drafts)))

    @app.post("/chat/new", include_in_schema=False)
    def chat_new(request: Request) -> Any:
        try:
            user = chat.user_from(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "staff":
            return RedirectResponse("/admin", status_code=303)
        sid = request.app.state.store.create_session(user["id"], "")
        return RedirectResponse(f"/chat?session={sid}", status_code=303)

    @app.get("/drafts", response_class=HTMLResponse, include_in_schema=False)
    def drafts(request: Request) -> Any:
        try:
            user = chat.user_from(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        if user["role"] != "staff":
            return RedirectResponse("/admin", status_code=303)
        rows = request.app.state.store.drafts_for(user["username"])
        return HTMLResponse(pages.drafts_page(user, rows))

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    return app


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn
    uvicorn.run("expo_trace.web.app:create_app", factory=True, host=host, port=port,
                reload=reload, log_level="info")
