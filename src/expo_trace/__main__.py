"""支持 ``python -m expo_trace``。"""

from __future__ import annotations

from expo_trace.main import main

if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
