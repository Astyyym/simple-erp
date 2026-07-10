@echo off
chcp 65001 >nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F
)
echo 已尝试停止消防ERP。
pause
