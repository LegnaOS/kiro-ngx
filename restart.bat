@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Kiro.py 一键部署 ===

:: 可选：传 --pull 参数时拉取代码
if "%~1"=="--pull" (
    echo [1/3] 拉取最新代码...
    git fetch origin
    git pull --ff-only 2>nul || echo   ff-only 失败，存在本地改动，跳过拉取
) else (
    echo [1/3] 跳过代码拉取（传 --pull 参数可启用）
)

:: venv 不存在时自动创建
if not exist "venv\Scripts\activate.bat" (
    echo [2/3] 创建虚拟环境...
    if exist venv rmdir /s /q venv
    python -m venv venv
) else (
    echo [2/3] 虚拟环境已存在
)
call venv\Scripts\activate.bat
pip install -q -r requirements.txt

:: 启动服务
echo [3/3] 启动服务...
set TARGET_PORT=8991
if defined PORT set TARGET_PORT=%PORT%
set PID_FILE=.kiro.pid

:: 先尝试 PID 文件
set OLD_PID=
if exist "%PID_FILE%" (
    set /p OLD_PID=<"%PID_FILE%"
)

:: PID 文件无效时按端口查找 LISTENING 进程
if "%OLD_PID%"=="" (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%TARGET_PORT% " ^| findstr "LISTENING"') do (
        set OLD_PID=%%a
    )
)

:: 停止旧进程
if not "%OLD_PID%"=="" (
    echo 停止旧进程 (PID: %OLD_PID%)...
    taskkill /PID %OLD_PID% /F >nul 2>&1
    :: 等待进程退出，最多 5 秒
    for /l %%i in (1,1,10) do (
        tasklist /FI "PID eq %OLD_PID%" 2>nul | findstr "%OLD_PID%" >nul 2>&1 || goto :stopped
        timeout /t 1 /nobreak >nul
    )
    :stopped
)

:: 后台启动服务
start "" /b cmd /c "venv\Scripts\python main.py > kiro.log 2>&1"

:: 获取新进程 PID 并写入 PID 文件
timeout /t 1 /nobreak >nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%TARGET_PORT% " ^| findstr "LISTENING"') do (
    echo %%a> "%PID_FILE%"
    echo 服务已启动 (PID: %%a), 日志: kiro.log
    goto :healthcheck
)
echo 服务已启动, 日志: kiro.log

:healthcheck
:: 健康检查：等待端口就绪
for /l %%i in (1,1,10) do (
    timeout /t 1 /nobreak >nul
    netstat -ano | findstr ":%TARGET_PORT% " | findstr "LISTENING" >nul 2>&1 && (
        echo 端口 %TARGET_PORT% 已就绪
        goto :done
    )
)
echo 警告: 端口 %TARGET_PORT% 在 10 秒内未就绪，请检查 kiro.log

:done
