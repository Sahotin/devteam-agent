#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_URL="http://127.0.0.1:8000"
HEALTH_URL="$APP_URL/api/v1/health"
cd "$PROJECT_ROOT"

echo ""
echo "  DevTeam Agent"
echo "  macOS Apple Silicon 本机服务"
echo ""

if [[ ! -x ".venv/bin/python" ]]; then
  echo "首次运行，正在安装本机依赖……"
  bash scripts/install_macos.sh
fi

if curl --silent --fail "$HEALTH_URL" >/dev/null 2>&1; then
  echo "服务已经启动，正在打开浏览器。"
  open "$APP_URL"
  exit 0
fi

echo "正在检查模型与数据库配置……"
.venv/bin/python -c 'from backend.app.core.config import Settings; s = Settings.from_env(); print(f"模型服务：{s.llm_provider} / {s.llm_model}")'

echo "正在启动服务：$APP_URL"
echo "需要停止时，请在此窗口按 Control+C。"
echo ""

(
  for _ in $(seq 1 60); do
    if curl --silent --fail "$HEALTH_URL" >/dev/null 2>&1; then
      open "$APP_URL"
      exit 0
    fi
    sleep 0.5
  done
  echo "服务启动超时，请查看当前窗口中的错误信息。"
) &

exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

