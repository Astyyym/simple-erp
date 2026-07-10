@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
if exist .venv (
  echo 环境已存在，跳过安装。
) else (
  py -3 -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
)
echo 初始化完成。
pause
