@echo off
setlocal
rem Build winclock probe binaries (x86 + x64) for hook verification.
cd /d "%~dp0..\.."
set VSPATH=
for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSPATH=%%i
if not defined VSPATH ( echo [ERROR] VS not found & exit /b 1 )
set SRC=%cd%\native\test\winclock_probe.c
set OUT=%cd%\tools\booster
call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul 2>&1
cl /nologo /MT /O2 /W3 /utf-8 "%SRC%" /Fe:"%OUT%\winclock32.exe" /Fo:"%cd%\native\obj\x86\\" user32.lib
if errorlevel 1 ( echo [ERROR] x86 probe failed & exit /b 1 )
call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul 2>&1
cl /nologo /MT /O2 /W3 /utf-8 "%SRC%" /Fe:"%OUT%\winclock64.exe" /Fo:"%cd%\native\obj\x64\\" user32.lib
if errorlevel 1 ( echo [ERROR] x64 probe failed & exit /b 1 )
echo [OK] winclock32.exe + winclock64.exe
