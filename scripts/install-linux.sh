#!/usr/bin/env bash
# 在离线 Linux x86_64 上安装依赖并检查环境。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKGS="$ROOT/offline_packages_linux"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "当前架构是 $(uname -m)，本离线包只适用于 x86_64。"
  exit 1
fi

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "需要 Python 3.12。请先安装 python3.12 / python3.12-venv。"
  exit 1
fi

if [[ ! -d "$PKGS" ]]; then
  echo "找不到离线包目录: $PKGS"
  exit 1
fi

cd "$ROOT"
python3.12 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --no-index --find-links="$PKGS" -r "$ROOT/requirements_frozen.txt"

echo
echo "安装完成。启动："
echo "  source $ROOT/.venv/bin/activate"
echo "  python -m backend.main"
echo
echo "请确认 .env 里的路径在本机真实存在，模型地址为："
echo "  http://143.21.64.150:4000/v1/messages"
