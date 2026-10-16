@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
rem 判定标准是「可用的 Windows 环境」，不是「.venv 目录存在」：
rem Linux/WSL 建的 .venv 只有 bin\ 没有 Scripts\python.exe，只看目录会把这里误判成已就绪。
if exist .venv\Scripts\python.exe (
  echo 环境已存在，跳过安装。
) else (
  if exist .venv (
    echo 检测到 .venv 不是可用的 Windows 环境（缺 Scripts\python.exe），删除后重建。
    rmdir /s /q .venv
  )
  rem 优先用打包脚本同款解释器（依赖已验证可装），没有才回退 py -3。
  py -V:Astral/CPython3.11.15 -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
)
echo 初始化完成。
pause
