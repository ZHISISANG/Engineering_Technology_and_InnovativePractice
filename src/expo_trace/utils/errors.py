"""统一异常类型。

约定：所有“可预期”的失败都继承 :class:`TraceError`，由 CLI 转成中文提示与退出码；
未预期异常照常抛栈，方便定位。
"""

from __future__ import annotations


class TraceError(Exception):
    """本工具的业务异常基类。"""

    exit_code = 2


class ConfigError(TraceError):
    """配置缺失、类型错误或触碰只读边界。"""


class InputError(TraceError):
    """导出文件缺失、损坏或缺少必需列。"""


class SnapshotFallback(Exception):
    """读取失败，已切换到上次快照（内部信号，不是最终错误）。"""

    def __init__(self, reason: str, snapshot_at: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.snapshot_at = snapshot_at


class BlockedAction(TraceError):
    """试图执行不可逆动作（写回、外部发布、自动决定）。"""

    exit_code = 5


class PausedError(TraceError):
    """试点处于暂停状态，拒绝生成新草稿。"""

    exit_code = 3


class ReviewRejected(TraceError):
    """人工确认被拒绝（权限、完整性或归属问题）。"""

    exit_code = 4
