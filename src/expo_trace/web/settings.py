"""阶段 2 Web 运行配置。

所有配置来自环境变量，默认值面向本机演示：
数据库落在 runs/web/app.db（已被 .gitignore 覆盖），线索数据只读复用 runs/*/output。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    db_path: Path
    run_dir: Path
    secret: str
    llm_provider: str
    embedder: str
    seed_password: str
    ttl_minutes: int = 720

    @classmethod
    def from_env(cls) -> "Settings":
        run_dir = _path("EXPO_RUN_DIR", ROOT / "runs" / "final")
        if not (run_dir / "output" / "tickets.jsonl").exists():
            run_dir = ROOT / "runs" / "demo"
        return cls(
            db_path=_path("EXPO_WEB_DB", ROOT / "runs" / "web" / "app.db"),
            run_dir=run_dir,
            secret=os.environ.get("EXPO_WEB_SECRET", "").strip() or "dev-only-secret-change-me",
            llm_provider=os.environ.get("EXPO_LLM_PROVIDER", "mock").strip() or "mock",
            embedder=os.environ.get("EXPO_EMBEDDER", "hash").strip() or "hash",
            seed_password=os.environ.get("EXPO_SEED_PASSWORD", "demo-1234"),
        )

    @property
    def is_default_secret(self) -> bool:
        return self.secret == "dev-only-secret-change-me"
