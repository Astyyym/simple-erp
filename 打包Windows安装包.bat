@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  简单ERP Windows 交付构建（一次出齐三种产物）
rem
rem  1) PyInstaller one-folder 产物  ->  dist\local-<版本>\简单ERP\
rem  2) Inno Setup 编译安装包        ->  dist\simple-erp-setup-<版本>.exe
rem  3) 交付 ZIP                     ->  dist\simple-erp-windows-<版本>.zip
rem
rem  三种形态缺一不可（AGENTS.md「交付习惯」）：
rem    绿色版目录 = 本机直接跑；安装包 = 门店用户装；ZIP = 发给别人
rem  版本只有一个真源：根目录 VERSION
rem ============================================================

set /p APPVER=<VERSION
if "%APPVER%"=="" (
  echo 读取 VERSION 失败。
  pause
  exit /b 1
)

set DISTDIR=dist\local-%APPVER%
set WORKDIR=build\local-%APPVER%

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

echo.
echo [1/3] 正在打包 one-folder 程序目录：%DISTDIR%\简单ERP
rem 用 python -m PyInstaller：MSYS 下直接调 pyinstaller.exe 可能没有输出
.venv-win\Scripts\python.exe -m PyInstaller 简单ERP.spec --clean --noconfirm --distpath %DISTDIR% --workpath %WORKDIR%
if errorlevel 1 (
  echo PyInstaller 打包失败。
  pause
  exit /b 1
)

rem 手动 --distpath 不会生成产物根 config.json（只有 _internal\config.json），必须补齐。
if not exist "%DISTDIR%\简单ERP\config.json" copy packaging\default_config.json "%DISTDIR%\简单ERP\config.json" >nul

set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo 未找到 Inno Setup 6 编译器 ISCC.exe。
  echo 安装方式：winget install --id JRSoftware.InnoSetup
  pause
  exit /b 1
)

echo.
echo [2/3] 正在编译安装包：dist\simple-erp-setup-%APPVER%.exe
"%ISCC%" /Qp /DAppVersion=%APPVER% /DSourceDir=%~dp0%DISTDIR%\简单ERP /DOutputDir=%~dp0dist packaging\installer\简单ERP.iss
if errorlevel 1 (
  echo 安装包编译失败。
  pause
  exit /b 1
)

echo.
echo [3/3] 正在生成交付 ZIP：dist\simple-erp-windows-%APPVER%.zip
.venv-win\Scripts\python.exe packaging\make_release_zip.py --version %APPVER%
if errorlevel 1 (
  echo ZIP 生成失败。
  pause
  exit /b 1
)

echo.
echo 构建完成，三种产物齐备：
echo   绿色版目录：%DISTDIR%\简单ERP\
echo   安装包　　：dist\simple-erp-setup-%APPVER%.exe
echo   交付 ZIP　：dist\simple-erp-windows-%APPVER%.zip
echo.
echo 安装包为 per-user 安装（%%LOCALAPPDATA%%\Programs\简单ERP），不需要管理员权限；
echo 业务数据在「文档\简单ERP数据」，安装/升级/卸载都不会删除。
pause
