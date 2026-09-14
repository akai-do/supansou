@echo off
setlocal
rem Booster build script: hook32.dll(x86) / hook64.dll(x64) / booster.exe(x64)
rem Requires VS 2022 (MSVC + Windows SDK). MinHook is vendored in third_party/minhook.
rem NOTE: keep this file ASCII-only (cmd parses batch files in ANSI codepage).

cd /d "%~dp0.."
set ROOT=%cd%
set OUT=%ROOT%\tools\booster
if not exist "%OUT%" mkdir "%OUT%"

set VSWHERE="%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist %VSWHERE% (
    echo [ERROR] vswhere.exe not found. Install Visual Studio 2022 first.
    exit /b 1
)
for /f "usebackq tokens=*" %%i in (`%VSWHERE% -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSPATH=%%i
if not defined VSPATH (
    echo [ERROR] No Visual Studio with C++ toolchain found.
    exit /b 1
)

set MINHOOK=%ROOT%\native\third_party\minhook
set HOOKSRC=%ROOT%\native\hook
set CTRLSRC=%ROOT%\native\controller
set OBJDIR=%ROOT%\native\obj
if not exist "%OBJDIR%\x86" mkdir "%OBJDIR%\x86"
if not exist "%OBJDIR%\x64" mkdir "%OBJDIR%\x64"
if not exist "%MINHOOK%\src\MinHook.h" copy "%MINHOOK%\include\MinHook.h" "%MINHOOK%\src\MinHook.h" >nul

rem ---- 1) hook32.dll (x86, primary: client is WOW64) ----
call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul 2>&1
if errorlevel 1 goto vsfail
cl /nologo /MT /O2 /W3 /EHsc /utf-8 /LD /I"%MINHOOK%\include" /I"%MINHOOK%\src" /I"%HOOKSRC%" "%HOOKSRC%\booster_hook.cpp" "%HOOKSRC%\dllmain.cpp" "%MINHOOK%\src\buffer.c" "%MINHOOK%\src\hook.c" "%MINHOOK%\src\trampoline.c" "%MINHOOK%\src\hde\hde32.c" "%MINHOOK%\src\hde\hde64.c" /Fe:"%OUT%\hook32.dll" /Fo:"%OBJDIR%\x86\\" user32.lib winmm.lib
if errorlevel 1 goto fail
echo [OK] hook32.dll

rem ---- 2) hook64.dll (x64) ----
call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul 2>&1
if errorlevel 1 goto vsfail
cl /nologo /MT /O2 /W3 /EHsc /utf-8 /LD /I"%MINHOOK%\include" /I"%MINHOOK%\src" /I"%HOOKSRC%" "%HOOKSRC%\booster_hook.cpp" "%HOOKSRC%\dllmain.cpp" "%MINHOOK%\src\buffer.c" "%MINHOOK%\src\hook.c" "%MINHOOK%\src\trampoline.c" "%MINHOOK%\src\hde\hde32.c" "%MINHOOK%\src\hde\hde64.c" /Fe:"%OUT%\hook64.dll" /Fo:"%OBJDIR%\x64\\" user32.lib winmm.lib
if errorlevel 1 goto fail
echo [OK] hook64.dll

rem ---- 3) booster.exe (x64 controller) ----
cl /nologo /MT /O2 /W3 /EHsc /utf-8 /std:c++20 /I"%HOOKSRC%" "%CTRLSRC%\main.cpp" "%CTRLSRC%\injector.cpp" "%CTRLSRC%\sharedmem.cpp" /Fe:"%OUT%\booster.exe" /Fo:"%OBJDIR%\x64\\" user32.lib
if errorlevel 1 goto fail
echo [OK] booster.exe

rem ---- 4) inject helpers (bitness-matched remote LoadLibrary) ----
rem A 64-bit booster.exe cannot CreateRemoteThread into a WOW64 target: the
rem LoadLibrary address it resolves lives in its own 64-bit kernel32 and is
rem meaningless in the 32-bit address space. The Baidu client is WOW64, so the
rem x86 helper is the one that actually matters in practice.
call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul 2>&1
cl /nologo /MT /O2 /W3 /EHsc /utf-8 "%CTRLSRC%\inject_helper.cpp" /Fe:"%OUT%\inject32.exe" /Fo:"%OBJDIR%\x86\\" /link /SUBSYSTEM:CONSOLE
if errorlevel 1 goto fail
echo [OK] inject32.exe
call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul 2>&1
cl /nologo /MT /O2 /W3 /EHsc /utf-8 "%CTRLSRC%\inject_helper.cpp" /Fe:"%OUT%\inject64.exe" /Fo:"%OBJDIR%\x64\\" /link /SUBSYSTEM:CONSOLE
if errorlevel 1 goto fail
echo [OK] inject64.exe

echo.
echo Build complete. Output: %OUT%
exit /b 0

:vsfail
echo [ERROR] vcvarsall failed.
exit /b 1
:fail
echo [ERROR] Build failed.
exit /b 1
