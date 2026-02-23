@echo off
chcp 65001 >nul
echo 正在停止 kiro.py (端口 8990)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8990 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul
echo 正在启动...
cd /d "%~dp0"
start "" python main.py
echo 已启动，访问 http://localhost:8990/admin/
