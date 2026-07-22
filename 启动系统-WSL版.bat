@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
set "ERP_URL=http://127.0.0.1:5001"
set "WSL_PROJECT=/mnt/d/wenjian/Hermes/简单ERP"

echo [%date% %time%] 正在启动简单ERP（WSL版）... >> logs\startup.log

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; try {$r=Invoke-WebRequest -UseBasicParsing '%ERP_URL%/health' -TimeoutSec 2; $ok=($r.StatusCode -eq 200)} catch {}; if ($ok) {Start-Process '%ERP_URL%'; exit 0} else {exit 1}"
if %errorlevel%==0 exit /b 0

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo 没找到 WSL，请先安装或启用 WSL。
  echo [%date% %time%] 没找到 WSL。 >> logs\startup.log
  pause
  exit /b 1
)

start "简单ERP WSL开发预览" /min wsl.exe bash -lc "cd '%WSL_PROJECT%' && ERP_PORT=5001 PYTHONPATH=app .venv/bin/python app/app.py >> logs/startup.log 2>&1"

echo 正在等待后台服务启动...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$url='%ERP_URL%/health'; for ($i=0; $i -lt 30; $i++) { try { $r=Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 1; if ($r.StatusCode -eq 200) { Start-Process '%ERP_URL%'; exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
  echo 简单ERP启动超时，请检查 logs\startup.log。
  echo [%date% %time%] 启动超时。 >> logs\startup.log
  pause
  exit /b 1
)
