@echo off
REM NovelGenerator — 一键构建 + 安装到模拟器
REM 前提: JDK 17+, Android SDK at ..\..\android-sdk, 模拟器已启动 (adb devices)

set PROJECT_DIR=%~dp0..
set ANDROID_DIR=%PROJECT_DIR%\android
set ANDROID_SDK=%PROJECT_DIR%\..\android-sdk
set JDK_DIR=C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot

if not exist "%JDK_DIR%\bin\java.exe" (
    echo [ERROR] JDK not found at %JDK_DIR%
    echo Please set JAVA_HOME manually.
    exit /b 1
)

set JAVA_HOME=%JDK_DIR%
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
