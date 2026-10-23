"""expo-ticket-trace：展商咨询工单的版本可追溯只读试点工具。

设计边界（硬约束，见 docs/design.md）：
- 只读：不写回源系统，不调用外部消息接口。
- 只出草稿：不自动对外发布收费、资格等决定。
- 可审计：每个结论都能回溯到版本、来源行与确认人。

公开入口在 :mod:`expo_trace.main`；领域逻辑在 :mod:`expo_trace.core`。
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
