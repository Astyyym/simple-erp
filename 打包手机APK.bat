@echo off
chcp 936 >nul
setlocal
set ROOT=%~dp0

if "%JAVA_HOME%"=="" set JAVA_HOME=D:\app\Android\jdk21
if "%ANDROID_HOME%"=="" set ANDROID_HOME=D:\app\Android\Sdk
set ANDROID_SDK_ROOT=%ANDROID_HOME%

if not exist "%JAVA_HOME%\bin\java.exe" (
  echo [错误] 找不到 JDK：%JAVA_HOME%
  echo        请安装 JDK 17+ 并设置 JAVA_HOME。
  pause
  exit /b 1
)

if not exist "%ANDROID_HOME%\platforms" (
  echo [错误] 找不到 Android SDK：%ANDROID_HOME%
  echo        请安装 Android SDK 并设置 ANDROID_HOME。
  pause
  exit /b 1
)

if not exist "%ROOT%native\gradlew.bat" (
  echo [错误] 找不到 native\gradlew.bat
  pause
  exit /b 1
)

set APPVER=1.0.0
set /p APPVER=<"%ROOT%native\VERSION"

echo.
echo [1/3] 构建 APK（原生客户端）
rem 用工程自带的 gradlew：它按 native\gradle\wrapper 的版本走（9.3.0）。
rem 不要用 gradle-cache 里那个 8.14.3 —— Kotlin 2.1 插件在 8.x 上解析不到，
rem 报错是 "Plugin org.jetbrains.kotlin.android not found"，看着像网络问题，其实是版本错。
call "%ROOT%native\gradlew.bat" -p "%ROOT%native" assembleRelease --no-daemon
if errorlevel 1 (
  echo.
  echo [错误] 构建失败，请检查上面的 Gradle 输出。
  pause
  exit /b 1
)

echo.
echo [2/3] 复制产物到 dist
rem APK 产物在**纯 ASCII 的 buildDir** 里，不再是 native\app\build。
rem 原因见 native\build.gradle：项目路径含中文会让 Gradle 的 worker argfile(UTF-8)
rem 被 java 启动器按 GBK 读，导致单元测试 ClassNotFoundException；因此把 buildDir
rem 挪出中文路径。ERP_BUILD_ROOT 与 build.gradle 里的默认值保持一致。
if "%ERP_BUILD_ROOT%"=="" set ERP_BUILD_ROOT=%LOCALAPPDATA%\simple-erp-build
set APP_APK=%ERP_BUILD_ROOT%\app\outputs\apk\release\app-release.apk
if not exist "%ROOT%dist" mkdir "%ROOT%dist"
copy /y "%APP_APK%" "%ROOT%dist\simple-erp-mobile-v%APPVER%.apk" >nul
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
echo   2) 把 APK 传到手机（微信文件传输助手 / QQ / 网盘），点开安装。
echo      也可以用数据线执行：adb install -r dist\simple-erp-mobile-v%APPVER%.apk
echo      提示“未知来源”时，允许一次即可。
echo   3) 打开 App，点“扫二维码”，扫电脑设置页「手机访问」里的二维码。
echo      扫不出来就手输地址（设置页显示的那个），再输 6 位配对码。
echo.
echo 注意：签名密钥在 native\keystore\simple-erp.jks，口令在 native\keystore.properties。
echo       换机器或重装系统前请备份这两个文件，否则新版本无法覆盖安装。
echo       包名 com.simpleerp.mobile 沿用旧版，可直接覆盖升级（不会多出一个 App）。
pause
