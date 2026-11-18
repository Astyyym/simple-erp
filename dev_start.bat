@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem [env] judge by .venv\Scripts\python.exe only, same as setup.bat.
if not exist .venv\Scripts\python.exe (
  if exist .venv rmdir /s /q .venv
  py -V:Astral/CPython3.11.15 -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
)
rem [deps] install only when requirements.txt changed; see tools\ensure_deps.py.
.venv\Scripts\python.exe tools\ensure_deps.py
if errorlevel 1 (
  echo 依赖安装失败。
  pause
  exit /b 1
)
.venv\Scripts\python.exe app\app.py
