@echo off
chcp 936 >nul
cd /d "%~dp0"

rem ============================================================
rem  简单ERP Windows 交付打包（一次出齐三种产物）
rem
rem  1) 交付用 one-folder（正常入口）  -> build\clean-<版本>\简单ERP
rem  2) Inno Setup 安装包              -> dist\simple-erp-setup-<版本>.exe
rem  3) 交付 ZIP（无演示数据）         -> dist\simple-erp-windows-<版本>.zip
rem  4) 绿色版 one-folder（演示入口）  -> dist\local-<版本>\简单ERP
rem  5) 绿色版写入 200 条演示数据      -> dist\local-<版本>\简单ERP\demo_data
rem
rem  ★ 用户 2026-10-14 决策：**只有绿色版**自带 200 条演示数据（打开即用）；
rem    安装包与 ZIP 是给门店/别人用的正式程序，不得含演示数据。
rem    所以安装包与 ZIP 从「正常入口」产物构建，绿色版用 简单ERP-demo.spec
rem    （入口 packaging\demo_entry.py：数据根强制指向包内 demo_data，
rem    与机器上的正式数据物理隔离，不会把演示数据写进用户真实数据根）。
rem  版本只有一处来源：根目录 VERSION
rem ============================================================

set /p APPVER=<VERSION
if "%APPVER%"=="" (
  echo 读取 VERSION 失败。
  pause
  exit /b 1
)

set GREENDIR=dist\local-%APPVER%
set CLEANDIR=build\clean-%APPVER%
set GREENWORK=build\local-%APPVER%
set CLEANWORK=build\cleanwork-%APPVER%
set GREENAPP=%GREENDIR%\简单ERP
set CLEANAPP=%CLEANDIR%\简单ERP

if not exist .venv-win\Scripts\python.exe (
  echo 正在创建 Windows 打包用的 .venv-win ...
  py -V:Astral/CPython3.11.15 -m venv .venv-win
  if errorlevel 1 py -3.14 -m venv .venv-win
  if errorlevel 1 (
    echo 创建 Windows Python 虚拟环境失败，请先安装可用的 Windows Python。
    pause
    exit /b 1
  )
)

echo 正在安装/更新依赖包...
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\pip.exe install -r requirements.txt pyinstaller pywebview
if errorlevel 1 (
  echo 依赖安装失败。
  pause
  exit /b 1
)

set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo 未找到 Inno Setup 6 的编译器 ISCC.exe。
  echo 安装方式：winget install --id JRSoftware.InnoSetup
  pause
  exit /b 1
)

rem 先清掉上一轮产物目录：PyInstaller --noconfirm 只清自己的输出目录，
rem 残留的 demo_data 会让第 5 步的 seed 以「目标库已存在」拒绝执行。
if exist "%CLEANDIR%" rmdir /s /q "%CLEANDIR%"
if exist "%GREENDIR%" rmdir /s /q "%GREENDIR%"

echo.
echo [1/5] 正在构建「交付用」one-folder 产物（正常入口）：%CLEANAPP%
rem 用 python -m PyInstaller：MSYS 下直接调 pyinstaller.exe 可能没有输出
.venv-win\Scripts\python.exe -m PyInstaller 简单ERP.spec --clean --noconfirm --distpath %CLEANDIR% --workpath %CLEANWORK%
if errorlevel 1 (
  echo PyInstaller 打包失败。
  pause
  exit /b 1
)

rem 手动 --distpath 不会生成包根 config.json（只有 _internal\config.json），这里补一份。
if not exist "%CLEANAPP%\config.json" copy packaging\default_config.json "%CLEANAPP%\config.json" >nul

echo.
echo [2/5] 正在编译安装包（不含演示数据）：dist\simple-erp-setup-%APPVER%.exe
"%ISCC%" /Qp /DAppVersion=%APPVER% /DSourceDir=%~dp0%CLEANAPP% /DOutputDir=%~dp0dist packaging\installer\简单ERP.iss
if errorlevel 1 (
  echo 安装包编译失败。
  pause
  exit /b 1
)

echo.
echo [3/5] 正在生成交付 ZIP（不含演示数据）：dist\simple-erp-windows-%APPVER%.zip
.venv-win\Scripts\python.exe packaging\make_release_zip.py --version %APPVER% --source "%CLEANAPP%"
if errorlevel 1 (
  echo ZIP 生成失败。
  pause
  exit /b 1
)

echo.
echo [4/5] 正在构建绿色版（演示入口）：%GREENAPP%
.venv-win\Scripts\python.exe -m PyInstaller 简单ERP-demo.spec --clean --noconfirm --distpath %GREENDIR% --workpath %GREENWORK%
if errorlevel 1 (
  echo 绿色版打包失败。
  pause
  exit /b 1
)
if not exist "%GREENAPP%\config.json" copy packaging\default_config.json "%GREENAPP%\config.json" >nul

echo.
echo [5/5] 正在往绿色版写入 200 条演示数据：%GREENAPP%\demo_data
.venv-win\Scripts\python.exe packaging\seed_demo_data.py --data-root "%GREENAPP%\demo_data" --documents 200 --config-path "%GREENAPP%\config.json"
if errorlevel 1 (
  echo 演示数据生成失败（校验不通过时脚本会返回非零）。
  pause
  exit /b 1
)

echo.
echo 打包完成，三种产物：
echo   绿色版目录（自带 200 条演示数据）：%GREENAPP%
echo   安装包（无演示数据）：dist\simple-erp-setup-%APPVER%.exe
echo   交付 ZIP（无演示数据）：dist\simple-erp-windows-%APPVER%.zip
echo.
echo 安装包为 per-user 安装（%%LOCALAPPDATA%%\Programs\简单ERP），不需要管理员权限；
echo 业务数据在「文档\简单ERP数据」，安装/升级/卸载都不动它。
echo 绿色版数据在包内 demo_data\，与「文档\简单ERP数据」相互独立，整个文件夹可拷走。
pause
