"""配置装载与“只读边界”校验。

原则：配置错误要在任何数据被处理之前失败，而不是跑到一半才拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from expo_trace.utils import miniyaml
from expo_trace.utils.errors import ConfigError

_FORBIDDEN_TRUE = {
    "safety.auto_publish": "自动对外发布",
    "safety.write_back": "写回源系统",
    "safety.external_message": "发送外部消息",
}


@dataclass
class Config:
    data: dict[str, Any]
    path: Path | None = None

    # ---- 读取 ----
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel or value is None:
            raise ConfigError(f"配置缺少必填项：{dotted}")
        return value

    @property
    def watermark(self) -> str:
        return str(self.get("safety.watermark", "【未确认 · 不可对外】"))

    @property
    def actor(self) -> str:
        return str(self.get("actor", "演练操作员"))

    def mapping(self, entity: str) -> dict[str, list[str]]:
        node = self.get(f"mapping.{entity}", {})
        if not isinstance(node, dict):
            raise ConfigError(f"mapping.{entity} 必须是映射")
        return {str(k): [str(v) for v in vals] for k, vals in node.items()}

    def reviewers(self) -> list[str]:
        return [str(x) for x in self.get("review.reviewers", [])]

    def pause_roles(self) -> list[str]:
        return [str(x) for x in self.get("review.pause_roles", [])]

    def owners(self) -> dict[str, str]:
        node = self.get("routing.owners", {})
        return {str(k): str(v) for k, v in (node or {}).items()}

    def blocking_codes(self) -> set[str]:
        return {str(x) for x in self.get("routing.blocking_codes", [])}

    # ---- 校验 ----
    def validate(self) -> None:
        if self.get("safety.read_only") is not True:
            raise ConfigError("safety.read_only 必须为 true：本工具不允许写回源系统")
        if self.get("safety.draft_only") is not True:
            raise ConfigError("safety.draft_only 必须为 true：本工具只生成待审核草稿")
        if str(self.get("pilot.mode", "read_only")) != "read_only":
            raise ConfigError("pilot.mode 只允许 read_only")
        for dotted, label in _FORBIDDEN_TRUE.items():
            if self.get(dotted) is True:
                raise ConfigError(f"只读边界被破坏：{dotted} 不能为 true（{label}）")
        if not self.watermark.strip():
            raise ConfigError("safety.watermark 不能为空：草稿必须带“未确认、不可对外”标识")
        for dotted in (
            "pilot.batch",
            "quality.backfill_hours",
            "quality.sample_size",
            "routing.sla_hours",
        ):
            self.require(dotted)
        if not self.reviewers():
            raise ConfigError("review.reviewers 不能为空：人工确认必须有具名角色")
        if not self.pause_roles():
            raise ConfigError("review.pause_roles 不能为空：审核方必须保留暂停权")
        if not self.get("review.required_confirmations"):
            raise ConfigError("review.required_confirmations 不能为空")
        if not self.blocking_codes():
            raise ConfigError("routing.blocking_codes 不能为空：兜底触发条件必须显式配置")


def load_config(path: str | Path) -> Config:
    data = miniyaml.load(path)
    cfg = Config(data=data, path=Path(path))
    cfg.validate()
    return cfg
