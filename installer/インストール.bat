@echo off
rem ND287 install - double-click this file on each PC.
rem The real work is in install.ps1 (same folder).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 pause
