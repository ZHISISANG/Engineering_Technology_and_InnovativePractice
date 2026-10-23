#!/usr/bin/env bash
# 本地部署：把已构建的 wheel 装进项目内 .venv，并做一次只读冒烟运行。
# 这个工具没有服务端；“部署”只意味着在本机装上 CLI，不连接任何生产系统。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

cd "$ROOT"

WHEEL="$(ls -1 dist/expo_ticket_trace-*.whl 2>/dev/null | tail -1 || true)"
if [[ -z "$WHEEL" ]]; then
  echo "找不到 dist/expo_ticket_trace-*.whl，请先运行：bash scripts/build.sh" >&2
  exit 2
fi
echo "wheel: $WHEEL"

VENV="$ROOT/.venv"
if [[ "$(uname -s 2>/dev/null || echo Windows)" == MINGW* || -d "$VENV/Scripts" ]]; then
  VENV_PY="$VENV/Scripts/python.exe"
else
  VENV_PY="$VENV/bin/python"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] 将创建 $VENV 并离线安装 $WHEEL"
  echo "[dry-run] 将运行 expo-trace status 冒烟"
  exit 0
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "==> 创建虚拟环境"
  "$PYTHON" -m venv "$VENV"
fi

echo "==> 离线安装（--no-index，不访问网络）"
"$VENV_PY" -m pip install --no-index --no-deps --force-reinstall "$WHEEL"

echo "==> 冒烟：CLI 可用、只读可跑"
"$VENV_PY" -m expo_trace --help >/dev/null
"$VENV_PY" -m expo_trace run \
  --export data/demo/export \
  --run-dir .deploy-smoke \
  --now "2026-10-20 10:00" >/dev/null

echo "==> 审计链自检"
"$VENV_PY" -m expo_trace audit verify --run-dir .deploy-smoke

rm -rf .deploy-smoke
echo
echo "部署完成：$VENV_PY -m expo_trace ..."
echo "上线前请先读 docs/pilot-runbook.md 的边界与暂停条款。"
