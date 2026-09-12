@echo off
chcp 65001 >nul
title DuPanSou-Archive 启动器
rem 在独立的最小化控制台中启动后端：关掉那个窗口才会停止服务，
rem 关闭本启动器窗口不影响服务运行。
cd /d "%~dp0backend"
start "DuPanSou-Archive 服务 (关闭此窗口=停止服务)" /min cmd /k python app.py
echo 服务已在新的最小化窗口中启动：http://localhost:5000
echo （停止服务：关闭那个最小化窗口，或双击 停止服务.bat）
timeout /t 3 >nul
