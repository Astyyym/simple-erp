@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
rem [env] judge by a usable Windows env, not by the .venv folder existing:
rem [env] a Linux/WSL .venv has bin\ but no Scripts\python.exe, so a folder check misfires.
if exist .venv\Scripts\python.exe (
  echo 环境已存在，跳过安装。
) else (
  if exist .venv (
    echo 检测到 .venv 不是可用的 Windows 环境（缺 Scripts\python.exe），删除后重建。
    rmdir /s /q .venv
  )
  rem [env] prefer the interpreter the packaging script uses; fall back to py -3.
  py -V:Astral/CPython3.11.15 -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
  rem [deps] reuse tools\ensure_deps.py (hashes requirements.txt);
  rem [deps] a fresh env always installs once, later runs skip fast.
  .venv\Scripts\python.exe tools\ensure_deps.py
  if errorlevel 1 (
    echo 依赖安装失败。
    pause
    exit /b 1
  )
)
echo 初始化完成。
pause
