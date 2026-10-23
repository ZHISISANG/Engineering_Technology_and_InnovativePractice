#!/usr/bin/env bash
# 验收自检：测试 + 示例 + 审计链 + 只读边界静态检查。
# 可在本地或 CI 里跑；失败即非零退出。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"
cd "$ROOT"

echo "==> 1. 语法"
"$PYTHON" -m compileall -q src tests examples

echo "==> 2. 全量测试"
"$PYTHON" -m unittest discover -s tests -t . 2>&1 | tail -4

echo "==> 3. 示例（含确认、登记、拒绝对外）"
PYTHONPATH=src "$PYTHON" examples/demo.py >/dev/null

echo "==> 4. 审计链"
PYTHONPATH=src "$PYTHON" -m expo_trace audit verify --run-dir examples/.demo-run

echo "==> 5. 只读边界"
FORBIDDEN='import (requests|httpx|smtplib|imaplib|paramiko|boto3)'
if grep -REn "$FORBIDDEN" src/ ; then
  echo "发现网络/邮件/远程执行依赖，违反只读边界" >&2
  exit 1
fi
PYTHONPATH=src "$PYTHON" - <<'PY'
from expo_trace.core import drafting
from expo_trace.utils.errors import BlockedAction

for name in ("publish", "send_external", "write_back"):
    try:
        getattr(drafting, name)()
    except BlockedAction:
        continue
    raise SystemExit(f"{name} 没有拒绝，违反不可逆边界")
print("对外动作全部被 BlockedAction 拒绝")
PY

echo "==> 6. 演示数据可重复运行"
PYTHONPATH=src "$PYTHON" -m expo_trace run \
  --export data/demo/export --run-dir .verify-run --now "2026-10-20 10:00" >/dev/null
PYTHONPATH=src "$PYTHON" -m expo_trace run \
  --export data/demo/export --run-dir .verify-run --now "2026-10-20 10:00" --strict >/dev/null
rm -rf .verify-run

echo
echo "全部自检通过。"
