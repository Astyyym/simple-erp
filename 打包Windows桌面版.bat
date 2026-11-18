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

echo 正在检查打包依赖...
rem [deps] base deps via tools\ensure_deps.py; it hashes requirements.txt and skips when unchanged.
rem [deps] pyinstaller/webview are checked separately so they are not reinstalled every run.
.venv-win\Scripts\python.exe tools\ensure_deps.py
if errorlevel 1 (
  echo 依赖安装失败。
  pause
  exit /b 1
)
.venv-win\Scripts\python.exe -c "import PyInstaller, webview" 2>nul
if errorlevel 1 (
  echo 正在安装打包专用依赖 ...
  .venv-win\Scripts\pip.exe install pyinstaller pywebview
  if errorlevel 1 (
    echo 打包依赖安装失败。
    pause
    exit /b 1
  )
)

rem [outdir] must include the version; must NOT reuse dist\local-<ver> because that
rem [outdir] path belongs to the demo/green build from the other packaging script.
rem [outdir] both scripts sharing one path would overwrite each other; this one uses dist\desktop-<ver>.
set /p APPVER=<VERSION
if "%APPVER%"=="" (
  echo 读取 VERSION 失败。
  pause
  exit /b 1
)
set OUTDIR=dist\desktop-%APPVER%
set WORKDIR=build\desktop-%APPVER%
if exist "%OUTDIR%" rmdir /s /q "%OUTDIR%"
rem [cleanup] drop desktop builds from older versions, keep only the current one.
for /d %%D in (dist\desktop-*) do if /i not "%%~nxD"=="desktop-%APPVER%" rmdir /s /q "%%~D"
for /d %%D in (build\desktop-*) do if /i not "%%~nxD"=="desktop-%APPVER%" rmdir /s /q "%%~D"

echo 正在打包 Windows 桌面版 EXE（%APPVER%）...
.venv-win\Scripts\python.exe -m PyInstaller 简单ERP.spec --clean --noconfirm --distpath %OUTDIR% --workpath %WORKDIR%
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)

if not exist "%OUTDIR%\简单ERP\config.json" copy packaging\default_config.json "%OUTDIR%\简单ERP\config.json" >nul

echo.
echo 打包完成：%OUTDIR%\简单ERP\简单ERP.exe
echo 可以把整个 %OUTDIR%\简单ERP 文件夹复制到目标电脑使用。
pause
