@echo off
REM NovelGenerator — 一键构建 + 安装到模拟器
REM 前提: JDK 17+, Android SDK (local.properties 或 ANDROID_HOME), 模拟器已启动 (adb devices)
REM v2.8.5: SDK 路径优先读 android/local.properties，不再写死绝对路径。

set PROJECT_DIR=%~dp0..
set ANDROID_DIR=%PROJECT_DIR%\android
set JDK_DIR=C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot

if not exist "%JDK_DIR%\bin\java.exe" (
    echo [ERROR] JDK not found at %JDK_DIR%
    echo Please set JAVA_HOME manually.
    exit /b 1
)

set JAVA_HOME=%JDK_DIR%

REM 解析 Android SDK：local.properties (sdk.dir=...) 优先，其次 ANDROID_HOME
set ANDROID_SDK=
if exist "%ANDROID_DIR%\local.properties" (
    for /f "tokens=1,* delims==" %%a in ('findstr /b "sdk.dir" "%ANDROID_DIR%\local.properties"') do set ANDROID_SDK=%%b
)
if not defined ANDROID_SDK if defined ANDROID_HOME set ANDROID_SDK=%ANDROID_HOME%
if not defined ANDROID_SDK set ANDROID_SDK=%PROJECT_DIR%\..\android-sdk
if not exist "%ANDROID_SDK%\platform-tools\adb.exe" (
    echo [ERROR] Android SDK not found at %ANDROID_SDK%
    echo Please set ANDROID_HOME or create android\local.properties (sdk.dir=...).
    exit /b 1
)

set ANDROID_HOME=%ANDROID_SDK%
set ANDROID_SDK_ROOT=%ANDROID_SDK%
set PATH=%JAVA_HOME%\bin;%ANDROID_SDK%\platform-tools;%PATH%

echo ========================================
echo   NovelGenerator - Build & Install
echo ========================================
echo JAVA_HOME: %JAVA_HOME%
echo ANDROID_HOME: %ANDROID_HOME%
echo.

echo [1/3] Syncing web files...
xcopy /Y /Q "%PROJECT_DIR%\web\index.html" "%ANDROID_DIR%\app\src\main\python\web\"
echo   Done.

echo [2/3] Building APK...
cd /d "%ANDROID_DIR%"
call gradlew.bat assembleDebug
if %ERRORLEVEL% neq 0 (
    echo [FAIL] Build failed!
    exit /b 1
)
echo   Build OK.

echo [3/3] Installing to device...
set APK=%ANDROID_DIR%\app\build\outputs\apk\debug\app-debug.apk
for /f "tokens=*" %%d in ('adb devices ^| findstr /v "List" ^| findstr "device"') do (
    echo   Device found, installing...
    adb install -r "%APK%"
    echo   Install complete!
    goto :done
)
echo   No device/emulator connected. APK ready at:
echo   %APK%
:done

echo.
echo ========================================
echo   Done!
echo ========================================
pause
