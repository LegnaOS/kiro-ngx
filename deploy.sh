#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Kiro.py 一键部署 ==="

# 拉取最新代码
echo "[1/4] 拉取最新代码..."
git pull --ff-only

# Python venv — 优先使用高版本 Python
PYTHON_BIN=""
for py in python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$py" &>/dev/null; then
    PYTHON_BIN="$py"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "错误: 未找到 python3"; exit 1
fi

if [ ! -d "venv" ]; then
  echo "[2/4] 创建虚拟环境 ($PYTHON_BIN)..."
  $PYTHON_BIN -m venv venv
else
  echo "[2/4] 虚拟环境已存在"
fi
source venv/bin/activate
pip install -q -r requirements.txt

# 构建前端（无 npm 则跳过，使用仓库中预编译的 admin-ui-dist）
if command -v npm &>/dev/null && [ -d "admin-ui" ]; then
  echo "[3/4] 构建前端..."
  cd admin-ui
  npm install --silent
  npm run build
  cd ..
else
  echo "[3/4] 跳过前端构建（使用预编译版本）"
fi

# 停止旧进程并启动
echo "[4/4] 启动服务..."
PID=$(lsof -ti :${PORT:-8990} 2>/dev/null || true)
if [ -n "$PID" ]; then
  echo "停止旧进程 (PID: $PID)..."
  kill $PID 2>/dev/null || true
  sleep 1
fi

nohup venv/bin/python main.py > kiro.log 2>&1 &
echo "服务已启动 (PID: $!), 日志: kiro.log"
