@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "BASH=C:\Program Files\Git\bin\bash.exe"
if not exist "%BASH%" set "BASH=C:\Program Files\Git\usr\bin\bash.exe"
if not exist "%BASH%" (
  echo 找不到 Git Bash，请先安装 Git for Windows。
  popd
  pause
  exit /b 1
)
"%BASH%" ./deploy-frontend.sh %*
set "ERR=%ERRORLEVEL%"
popd
echo.
if "%ERR%"=="0" (echo 前端部署完成。) else (echo 前端部署失败，退出码 %ERR%。)
pause
exit /b %ERR%
