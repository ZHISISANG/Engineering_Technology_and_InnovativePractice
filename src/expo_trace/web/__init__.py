"""阶段 2 Web 层（FastAPI + SQLite + SSE）。

依赖是可选的：`pip install -e ".[web]"` 才会装上 fastapi/uvicorn/python-multipart。
CLI 与只读工具在没有 Web 依赖时仍可正常运行。
"""

from .app import create_app

__all__ = ["create_app"]
