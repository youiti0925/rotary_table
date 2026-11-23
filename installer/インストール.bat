@echo off
rem ND287 install - double-click this file on each PC.
rem The app exe installs itself (no PowerShell: company security software blocks it).
if not exist "%~dp0app\nd287_bunkatsu.exe" (
    echo app\nd287_bunkatsu.exe not found. Copy the whole folder and try again.
    pause
    exit /b 1
)
start "" /wait "%~dp0app\nd287_bunkatsu.exe" --install %*
if errorlevel 1 (
    echo Install failed. See the message window and install.log.
    pause
    exit /b 1
)
