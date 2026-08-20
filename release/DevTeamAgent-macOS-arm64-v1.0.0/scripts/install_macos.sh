#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "  DevTeam Agent · macOS 安装程序"
echo "  项目目录：$PROJECT_ROOT"
echo ""

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "错误：此安装脚本仅适用于 macOS。"
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "提示：当前设备不是 Apple Silicon，仍将继续尝试安装。"
fi

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "错误：没有找到 Python 3.11 或更高版本。"
  echo "请先安装 Homebrew，然后执行：brew install python@3.12 git node"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "错误：没有找到 Git。"
  echo "请执行 xcode-select --install，或使用 Homebrew 安装 Git。"
  exit 1
fi

echo "[1/5] 使用 $($PYTHON_BIN --version) 创建本机虚拟环境"
if [[ ! -x ".venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

echo "[2/5] 安装后端依赖"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .

echo "[3/5] 创建运行目录"
mkdir -p data workspace

echo "[4/5] 准备本机配置"
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "      已创建 .env；当前默认使用演示模型，不包含任何 API Key。"
else
  echo "      已保留现有 .env。"
fi

echo "[5/5] 检查应用配置"
.venv/bin/python -c 'from backend.app.core.config import Settings; s = Settings.from_env(); print(f"      模型服务：{s.llm_provider} / {s.llm_model}")'
chmod +x scripts/install_macos.sh scripts/start_macos.sh "启动 DevTeam Agent.command"

echo ""
echo "安装完成。"
echo "如需使用 DeepSeek，请先按照《macOS部署说明.md》填写 .env。"
echo "之后可双击“启动 DevTeam Agent.command”，或执行：bash scripts/start_macos.sh"
echo ""

