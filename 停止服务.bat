@echo off
chcp 65001 >nul
rem 停止 DuPanSou-Archive：结束监听 5000 端口的进程
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 .*LISTENING"') do (
    echo 结束进程 PID=%%a
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="0" echo 5000 端口没有服务在运行。
timeout /t 2 >nul
