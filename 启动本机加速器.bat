@echo off
chcp 65001 >nul
title DuPanSou 下载提速 . 本机加速器 (127.0.0.1:5001)
cd /d "%~dp0"
rem 独立的本机加速器：给 Docker 版网页(localhost:5000)提供 Hook 注入能力。
rem 关掉这个窗口 = 停止加速服务。
set "ACCEL_NO_BACKGROUND=1"
set "HOST=127.0.0.1"
set "PORT=5001"
set "ACCEL_TOKEN=XQhvYFcjvHhjkRv5Jt1I6oN0OLV5lkmN"
set "ACCEL_GUEST=1"
set "ACCEL_GUEST_DAILY=50"
set "ACCEL_CORS_ORIGINS=http://localhost:5000,http://127.0.0.1:5000,https://xiaowusu.com,https://www.xiaowusu.com"
set "PYTHONIOENCODING=utf-8"
rem 不依赖 PATH：优先用本机安装的 Python 绝对路径
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
echo.
echo   ==========================================================
echo    DuPanSou 本机加速器（下载提速）正在启动...
echo   ==========================================================
echo.
echo    * 监听地址：http://127.0.0.1:5001
echo    * 网页(localhost:5000)会自动连到这里；请保持窗口开着。
echo    * 前提：百度网盘客户端正在运行，且有正在下载的任务。
echo.
cd backend
"%PY%" app.py
echo.
echo   服务已停止。按任意键关闭窗口。
pause >nul
