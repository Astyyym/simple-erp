@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] 正在启动消防ERP... >> logs\startup.log
powershell -NoProfile -ExecutionPolicy Bypass -Command "$r=$null; try {$r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/health -TimeoutSec 2} catch {}; if ($r -and $r.StatusCode -eq 200) {Start-Process http://127.0.0.1:5000; exit 0} else {exit 1}"
if %errorlevel%==0 exit /b 0
if not exist .venv\Scripts\python.exe (
  echo 请先运行 setup.bat 初始化环境。 >> logs\startup.log
  echo 请先运行 setup.bat 初始化环境。
  pause
  exit /b 1
)
start "消防ERP" /min .venv\Scripts\python.exe app\app.py
timeout /t 3 >nul
start http://127.0.0.1:5000
