"""FastAPI 应用装配：路由、静态资源、异常格式与安全响应头。

启动方式见 docs/phase2/chat-api.md：
    expo-trace web --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
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

    @app.get("/login", include_in_schema=False)
    def login_alias() -> RedirectResponse:
        return RedirectResponse("/admin/login", status_code=303)

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    def chat_page(request: Request) -> Any:
        try:
            user = chat.user_from(request)
        except HTTPException:
            return RedirectResponse("/admin/login", status_code=303)
        return HTMLResponse(pages.chat_page(user))

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    return app


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn
    uvicorn.run("expo_trace.web.app:create_app", factory=True, host=host, port=port,
                reload=reload, log_level="info")
