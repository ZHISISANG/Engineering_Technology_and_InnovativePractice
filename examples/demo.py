#!/usr/bin/env python3
"""端到端示例：跑一遍演示数据，并演示“确认 / 退回 / 登记 / 审计 / 拒绝对外”。

运行：
    PYTHONPATH=src python examples/demo.py

脚本只写 examples/.demo-run/，不修改 data/，不需要安装依赖。
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from expo_trace.core.audit import AuditLog  # noqa: E402
from expo_trace.core.drafting import publish  # noqa: E402
from expo_trace.core.feedback import submit_feedback  # noqa: E402
from expo_trace.core.pipeline import execute  # noqa: E402
from expo_trace.core.review import submit_review  # noqa: E402
from expo_trace.utils.errors import BlockedAction, ReviewRejected  # noqa: E402
from expo_trace.utils.textutil import ensure_utf8_stdio  # noqa: E402
from expo_trace.utils.timeutil import LOCAL_TZ  # noqa: E402

EXPORT = ROOT / "data" / "demo" / "export"
CONFIG = ROOT / "config" / "default.yaml"
RUN_DIR = ROOT / "examples" / ".demo-run"
NOW = datetime(2026, 10, 20, 10, 0, tzinfo=LOCAL_TZ)


def line(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


def main() -> int:
    ensure_utf8_stdio()
    # 每次从干净目录开始：确认记录是追加写的，重复跑同一时间的脚本会被去重逻辑拒绝。
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)

    line("1. 第一次运行：只读导入 → 版本判定 → 草稿/队列")
    result = execute(
        export_dir=EXPORT,
        run_dir=RUN_DIR,
        config_path=CONFIG,
        actor="示例脚本",
        now=NOW,
        run_name="demo",
    )
    counts = result.manifest["counts"]
    print(f"工单 {counts['tickets']} · 版本 {counts['versions']} · 问题 {counts['issues']} · 队列 {counts['queue']}")
    print(f"可出草稿 {sum(1 for d in result.dispositions.values() if d.value == 'draft_ready')}")
    print(f"人工队列 {sum(1 for d in result.dispositions.values() if d.value == 'human_review')}")
    print(f"边界阻塞 {sum(1 for d in result.dispositions.values() if d.value == 'blocked')}")

    line("2. 三项指标（并列，不允许只看工时）")
    metrics = result.metrics
    handling = metrics["handling"]
    rework = metrics["rework"]
    confirmation = metrics["confirmation"]
    print(f"处理工时   {handling['total_minutes']} 分钟（干净样本 {handling['clean_total_minutes']}，回填剔除 {handling['backfilled_excluded']}）")
    print(f"返工次数   {rework['total']} 次（退回 {rework['returned']}，重复确认 {rework['repeated_reviews']}）")
    print(f"确认完整率 {confirmation['rate']:.1%}（{confirmation['covered_items']}/{confirmation['required_items']} 项）")

    line("3. 不可逆边界：从代码层拒绝对外发布")
    try:
        publish(result.drafts["T-1001"], channel="exhibitor-email")
    except BlockedAction as exc:
        print(f"已拒绝：{exc}（退出码 {exc.exit_code}）")

    line("4. 人工确认：缺一项会被拒绝")
    draft = result.drafts["T-1001"]
    try:
        submit_review(
            result.store,
            result.config,
            ticket_id="T-1001",
            reviewer="傅景",
            decision="confirm",
            note="只确认两项",
            confirmed_items=["版本可定位", "来源可追溯"],
            draft=draft,
            now=NOW,
        )
    except ReviewRejected as exc:
        print(f"拒绝：{exc}")

    record = submit_review(
        result.store,
        result.config,
        ticket_id="T-1001",
        reviewer="傅景",
        decision="confirm",
        note="版本、来源、未确认项与用途均核对完毕，同意作为内部草稿",
        confirmed_items=list(result.config.get("review.required_confirmations", [])),
        draft=draft,
        now=NOW,
    )
    print(f"已确认：{record['review_id']}，草稿摘要 {record['draft_digest'][:12]}…")

    line("5. 登记误报（周会复盘用）")
    feedback = submit_feedback(
        result.store,
        result.config,
        ticket_id="T-1005",
        kind="误报",
        reporter="傅景",
        note="业务已确认两个版本可并存，工具判为冲突过严",
        now=NOW,
    )
    print(f"已登记：{feedback['feedback_id']}（{feedback['kind_label']}）")

    line("6. 重跑：确认与登记都进入报告与审计")
    result = execute(
        export_dir=EXPORT,
        run_dir=RUN_DIR,
        config_path=CONFIG,
        actor="示例脚本",
        now=NOW,
        run_name="demo",
    )
    print(f"确认记录 {result.metrics['confirmation']['confirmed_reviews']} 条，"
          f"完整率 {result.metrics['confirmation']['rate']:.1%}")
    print(f"误报/漏报/未确认 {result.metrics['feedback']['total']} 条")
    verify = AuditLog(result.store, actor="示例脚本").verify()
    print(f"审计链 {'完整' if verify['ok'] else '断裂'}：{verify['entries']} 条"
          + (f"，断点 {verify['broken_at']}" if not verify["ok"] else ""))

    line("7. 产物")
    for name in ("report.md", "checklist.md", "queue.csv", "issues.csv", "metrics.json"):
        path = result.store.path("output", name)
        print(f"{path.relative_to(ROOT)}  ({path.stat().st_size} bytes)")
    print(f"\n完整产物目录：{RUN_DIR.relative_to(ROOT)}/output/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
