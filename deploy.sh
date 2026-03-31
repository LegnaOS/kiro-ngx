#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Kiro.py 一键部署 ==="

# 可选：传 --pull 参数时才拉取代码
if [ "${1:-}" = "--pull" ]; then
  echo "[1/3] 拉取最新代码..."
  git fetch origin
  if ! git pull --ff-only 2>/dev/null; then
    echo "  ff-only 失败，存在本地改动，跳过拉取（如需强制更新请手动处理）"
  fi
else
  echo "[1/3] 跳过代码拉取（传 --pull 参数可启用）"
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
  echo "[2/3] 创建虚拟环境 ($PYTHON_BIN)..."
  rm -rf venv
  $PYTHON_BIN -m venv venv
else
  echo "[2/3] 虚拟环境已存在"
fi
source venv/bin/activate
pip install -q -r requirements.txt

# 前端：直接使用仓库预编译的 dist，不构建
echo "[3/3] 启动服务..."
TARGET_PORT=${PORT:-8991}
PID_FILE=".kiro.pid"

# 先尝试 PID 文件
PID=""
if [ -f "$PID_FILE" ]; then
  SAVED_PID=$(cat "$PID_FILE" 2>/dev/null)
  if [ -n "$SAVED_PID" ] && kill -0 "$SAVED_PID" 2>/dev/null; then
    PID="$SAVED_PID"
  fi
fi

# PID 文件无效时按端口查找（只匹配 LISTEN 状态，避免误杀客户端连接）
if [ -z "$PID" ]; then
  if command -v lsof &>/dev/null; then
    PID=$(lsof -i :"$TARGET_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
  elif command -v ss &>/dev/null; then
    PID=$(ss -tlnp "sport = :$TARGET_PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  elif command -v fuser &>/dev/null; then
    PID=$(fuser "$TARGET_PORT/tcp" 2>/dev/null | tr -d ' ' || true)
  fi
fi

if [ -n "$PID" ]; then
  echo "停止旧进程 (PID: $PID)..."
  kill "$PID" 2>/dev/null || true
  # 等待进程退出，最多 5 秒
  for i in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
fi

nohup venv/bin/python main.py > kiro.log 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
echo "服务已启动 (PID: $NEW_PID), 日志: kiro.log"

# 简单健康检查：等待端口就绪
for i in $(seq 1 10); do
  sleep 1
  if command -v lsof &>/dev/null; then
    lsof -ti :"$TARGET_PORT" &>/dev/null && echo "端口 $TARGET_PORT 已就绪" && exit 0
  elif command -v ss &>/dev/null; then
    ss -tlnp "sport = :$TARGET_PORT" 2>/dev/null | grep -q "$TARGET_PORT" && echo "端口 $TARGET_PORT 已就绪" && exit 0
  else
    # 无工具可用，直接信任启动
    exit 0
  fi
done
echo "警告: 端口 $TARGET_PORT 在 10 秒内未就绪，请检查 kiro.log"
