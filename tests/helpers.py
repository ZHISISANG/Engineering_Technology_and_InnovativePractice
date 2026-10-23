"""测试公共夹具：统一路径、固定时间、演示数据。"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # 让测试无需安装即可跑
    sys.path.insert(0, str(SRC))

from expo_trace.core.models import KnowledgeVersion, Ticket  # noqa: E402
from expo_trace.core.pipeline import execute  # noqa: E402
from expo_trace.utils.timeutil import LOCAL_TZ, parse_time  # noqa: E402

DEMO_EXPORT = ROOT / "data" / "demo" / "export"
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"
FIXED_NOW = datetime(2026, 10, 20, 10, 0, tzinfo=LOCAL_TZ)

# Windows 沙箱不允许在系统临时目录建目录；且 Python 3.14 的 mkdtemp 会写入受限 ACL，
# 导致子目录无法创建。测试自带一个简单的临时目录，用完即删。
TMP_ROOT = ROOT / ".tmp"
_TMP_COUNTER = itertools.count(1)


class _TempDir:
    def __init__(self) -> None:
        TMP_ROOT.mkdir(exist_ok=True)
        self.name = str(TMP_ROOT / f"t{os.getpid()}_{next(_TMP_COUNTER)}")
        Path(self.name).mkdir()

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *_exc: object) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)


def temp_dir() -> _TempDir:
    return _TempDir()


def run_demo(run_dir: str | Path, *, simulate_failure: bool = False, now: datetime = FIXED_NOW):
    return execute(
        export_dir=DEMO_EXPORT,
        run_dir=run_dir,
        config_path=DEFAULT_CONFIG,
        now=now,
        simulate_failure=simulate_failure,
    )


def version(
    version_id: str,
    *,
    start: str = "2026-07-01",
    end: str | None = "2026-12-31",
    confirmed: bool = True,
    category: str = "booth",
    batch: str = "2026-S1",
    **kwargs,
) -> KnowledgeVersion:
    return KnowledgeVersion(
        version_id=version_id,
        notice_id=f"N-{version_id}",
        batch=batch,
        category=category,
        effective_from=parse_time(start).value,  # type: ignore[arg-type]
        effective_to=parse_time(end).value if end else None,  # type: ignore[arg-type]
        confirmed=confirmed,
        **kwargs,
    )


def ticket(
    ticket_id: str = "T-1",
    *,
    occurred: str = "2026-10-12 09:00",
    received: str | None = None,
    batch: str = "2026-S1",
    category: str = "booth",
) -> Ticket:
    return Ticket(
        ticket_id=ticket_id,
        batch=batch,
        category=category,
        question="测试问题",
        occurred=parse_time(occurred),
        received=parse_time(received or occurred),
        source="service_desk.csv",
        line=2,
    )
