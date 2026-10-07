@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv-win\Scripts\python.exe (
  echo 正在创建 Windows 打包环境 .venv-win ...
  py -V:Astral/CPython3.11.15 -m venv .venv-win
  if errorlevel 1 py -3.14 -m venv .venv-win
  if errorlevel 1 (
    echo 创建 Windows Python 虚拟环境失败，请先安装可用的 Windows Python。
    pause
    exit /b 1
  )
)

echo 正在安装/检查打包依赖...
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\pip.exe install -r requirements.txt pyinstaller pywebview
if errorlevel 1 (
  echo 依赖安装失败。
  pause
  exit /b 1
)

echo 正在打包 Windows 桌面版 EXE...
.venv-win\Scripts\pyinstaller.exe 简单ERP.spec --clean --noconfirm
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)

if not exist dist\简单ERP\config.json copy packaging\default_config.json dist\简单ERP\config.json >nul

echo.
echo 打包完成：dist\简单ERP\简单ERP.exe
echo 可以把整个 dist\简单ERP 文件夹复制到目标电脑使用。
pause
