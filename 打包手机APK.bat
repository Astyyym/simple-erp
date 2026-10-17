@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
set ROOT=%~dp0

if "%JAVA_HOME%"=="" set JAVA_HOME=D:\app\Android\jdk21
if "%ANDROID_HOME%"=="" set ANDROID_HOME=D:\app\Android\Sdk
if "%GRADLE_USER_HOME%"=="" set GRADLE_USER_HOME=D:\app\Android\gradle-cache
set ANDROID_SDK_ROOT=%ANDROID_HOME%

if not exist "%JAVA_HOME%\bin\java.exe" (
  echo [错误] 找不到 JDK：%JAVA_HOME%
  echo         请安装 JDK 17+ 或设置 JAVA_HOME。
  pause
  exit /b 1
)

if not exist "%ANDROID_HOME%\platforms" (
  echo [错误] 找不到 Android SDK：%ANDROID_HOME%
  echo         请安装 Android SDK 或设置 ANDROID_HOME。
  pause
  exit /b 1
)

set GRADLE_EXE=
for /d %%D in ("%GRADLE_USER_HOME%\wrapper\dists\gradle-*") do (
  for /d %%H in ("%%D\*") do (
    for /d %%E in ("%%H\gradle-*") do (
      if exist "%%E\bin\gradle.bat" set GRADLE_EXE=%%E\bin\gradle.bat
    )
  )
)

if "%GRADLE_EXE%"=="" (
  echo [错误] 找不到 Gradle 分发：%GRADLE_USER_HOME%\wrapper\dists
  echo         首次构建需要联网让 Gradle 自行下载，或把 gradle 加入 PATH。
  pause
  exit /b 1
)

set APPVER=1.0.0
set /p APPVER=<"%ROOT%apk\VERSION"

echo.
echo [1/3] 构建 APK
call "%GRADLE_EXE%" assembleRelease --offline --no-daemon -p "%ROOT%apk"
if errorlevel 1 (
  echo.
  echo [错误] 构建失败，看上面的 Gradle 输出。
  pause
  exit /b 1
)

echo.
echo [2/3] 复制产物到 dist
if not exist "%ROOT%dist" mkdir "%ROOT%dist"
copy /y "%ROOT%apk\app\build\outputs\apk\release\app-release.apk" "%ROOT%dist\simple-erp-mobile-v%APPVER%.apk" >nul
if errorlevel 1 (
  echo [错误] 复制失败。
  pause
  exit /b 1
)

echo.
echo [3/3] 完成：dist\simple-erp-mobile-v%APPVER%.apk
echo.
echo 装到手机：
echo   1) 手机和电脑连同一个 WiFi
echo   2) 把 APK 发到手机（微信文件传输助手 / QQ / 网盘）后点开安装；
echo      或数据线连电脑执行：adb install -r dist\simple-erp-mobile-v%APPVER%.apk
echo      提示「未知来源」时允许这一次即可
echo   3) 打开 App 填电脑地址（设置 - 手机访问 里显示的那个，如 192.168.10.5:5001），
echo      再在电脑上点「生成配对码」，把 6 位数字填进手机
echo.
echo 注意：签名密钥是 apk\keystore\simple-erp.jks，口令在 apk\keystore.properties。
echo       换电脑或重装系统前务必备份这两个文件，否则新版本无法覆盖安装。
pause
