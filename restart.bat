@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 正在停止服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8991 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo 构建前端...
cd admin-ui
call npm run build
cd ..

echo 启动服务...
start "" python main.py
echo 已启动，访问 http://localhost:8991/admin/
