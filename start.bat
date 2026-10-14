@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\13367\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo.
echo   Starting Switchboard ...
echo   Web console: http://127.0.0.1:8000
echo.
"%PY%" run.py
pause
