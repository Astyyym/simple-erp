@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ERP_URL=http://127.0.0.1:5000"
set "WSL_PROJECT=/mnt/d/wenjian/Hermes/ERP系统"

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo 没找到 WSL。
  pause
  exit /b 1
)

wsl.exe bash -lc "pid=$(lsof -ti tcp:5000 2>/dev/null | head -n 1); if [ -n \"$pid\" ]; then kill $pid; echo 已停止消防ERP。; else echo 消防ERP后台服务没有运行。; fi"
pause
