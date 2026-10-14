@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\13367\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" scripts/service.py %*
pause
