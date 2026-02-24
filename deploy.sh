#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Kiro.py 一键部署 ==="

# 拉取最新代码（自动处理本地冲突）
echo "[1/4] 拉取最新代码..."
git fetch origin
if ! git pull --ff-only 2>/dev/null; then
  echo "  ff-only 失败，重置到远程版本..."
  git reset --hard origin/master
fi

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

# venv 损坏或不存在时自动重建
if [ ! -f "venv/bin/activate" ]; then
  echo "[2/4] 创建虚拟环境 ($PYTHON_BIN)..."
  rm -rf venv
  $PYTHON_BIN -m venv venv
else
  echo "[2/4] 虚拟环境已存在"
fi
source venv/bin/activate
pip install -q -r requirements.txt

# 前端：Linux 直接使用仓库预编译的 dist，不构建
echo "[3/4] 使用预编译前端"

# 停止旧进程并启动（兼容 CentOS/Ubuntu：优先 ss → fuser → lsof）
echo "[4/4] 启动服务..."
TARGET_PORT=${PORT:-8990}
PID=""
if command -v ss &>/dev/null; then
  PID=$(ss -tlnp "sport = :$TARGET_PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)
elif command -v fuser &>/dev/null; then
  PID=$(fuser "$TARGET_PORT/tcp" 2>/dev/null | tr -d ' ')
elif command -v lsof &>/dev/null; then
  PID=$(lsof -ti :"$TARGET_PORT" 2>/dev/null || true)
fi
if [ -n "$PID" ]; then
  echo "停止旧进程 (PID: $PID)..."
  kill $PID 2>/dev/null || true
  sleep 1
fi

nohup venv/bin/python main.py > kiro.log 2>&1 &
echo "服务已启动 (PID: $!), 日志: kiro.log"
