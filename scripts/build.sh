#!/usr/bin/env bash
# 构建：编译检查 → 全量测试 → 跑一遍示例 → 打 wheel（可选）。
# 无第三方运行时依赖；wheel 需要本机有 setuptools（或允许构建隔离联网）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"

cd "$ROOT"

echo "==> 解释器"
"$PYTHON" -V
"$PYTHON" -c "import sys; print('platform:', sys.platform)"

echo "==> [1/4] 语法编译"
"$PYTHON" -m compileall -q src tests examples

echo "==> [2/4] 全量测试"
"$PYTHON" -m unittest discover -s tests -t . 2>&1 | tail -5

echo "==> [3/4] 示例端到端"
PYTHONPATH=src "$PYTHON" examples/demo.py >/dev/null
echo "示例通过（产物在 examples/.demo-run/）"

echo "==> [4/4] 构建 wheel"
if "$PYTHON" -c "import setuptools" >/dev/null 2>&1; then
  rm -rf dist
  "$PYTHON" -m pip wheel . --no-deps -w dist
  ls -1 dist
else
  echo "跳过 wheel：当前解释器没有 setuptools。"
  echo "如需产物：python -m pip install setuptools && bash scripts/build.sh"
fi

echo
echo "构建完成。"
