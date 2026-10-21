@echo off
chcp 65001 >nul
title 停止 DuPanSou 本机加速器
echo 正在停止监听 5001 端口的本机加速器...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5001" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)
echo 完成。若之前未运行则无影响。
timeout /t 2 >nul
