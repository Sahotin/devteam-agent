#!/bin/bash
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"
bash scripts/start_macos.sh
STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  echo ""
  echo "启动失败，错误码：$STATUS"
  echo "按回车键关闭窗口。"
  read -r
fi
exit $STATUS

