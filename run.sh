#!/usr/bin/env bash
# 求职决策 Agent 演示启动脚本：检查依赖 → 启动本机服务（127.0.0.1:8899）
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import flask, requests, pypdf, docx" >/dev/null 2>&1; then
  echo "正在安装本地依赖…"
  python3 -m pip install -r requirements.txt
fi

exec python3 app.py
