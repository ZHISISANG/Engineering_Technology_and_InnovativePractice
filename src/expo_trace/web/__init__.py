"""阶段 2 Web 层（FastAPI + SQLite + SSE）。

依赖是可选的：`pip install -e ".[web]"` 才会装上 fastapi/uvicorn/python-multipart。
CLI 与只读工具在没有 Web 依赖时仍可正常运行。

`create_app` 用惰性导入暴露：`import expo_trace.web.settings` 这类纯配置/纯数据模块
不应该因为没装 fastapi 就导入失败。
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
