@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   Agnes 生成工作台 - 启动中
echo ============================================

REM 端口占用检查（8000 被占时给出明确提示，避免 silent failure）
netstat -ano | findstr :8000 | findstr LISTENING >nul
if %errorlevel%==0 (
    echo.
    echo [提示] 端口 8000 已被占用，服务可能已经在运行。
    echo 请先访问 http://127.0.0.1:8000 确认；若要重启，
    echo 先结束旧进程：netstat -ano ^| findstr :8000 查到 PID 后 taskkill /PID PID /F
    echo.
    pause
    exit /b 1
)

REM 使用项目自带的虚拟环境（.venv），%~dp0 保证从任何位置双击都有效
set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo [错误] 未找到项目虚拟环境 .venv\Scripts\python.exe
    echo 请先在项目目录执行：python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo 正在启动服务... 浏览器将在几秒后自动打开。
echo 停止服务：按 Ctrl+C，或直接关闭本窗口。
echo.

REM 4 秒后自动打开浏览器（等待服务就绪）
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:8000'"

"%PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000

echo.
echo ============================================
echo   服务已停止。如有报错请截图保留。
echo ============================================
pause >nul
