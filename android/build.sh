#!/bin/bash
# Build NovelGenerator Android APK
# Prerequisites: Android SDK (ANDROID_SDK below), JDK 17+
# v2.8.5: SDK 路径改为自动探测（local.properties > ANDROID_HOME > 常见位置），
# 不再写死绝对路径（原 C:\Users\Yan Zhao\deepseek\android-sdk 不存在导致构建必失败）。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ANDROID_DIR="$PROJECT_DIR/android"

# 1) local.properties 中的 sdk.dir（最高优先）
if [ -f "$ANDROID_DIR/local.properties" ]; then
    SDK_FROM_PROP="$(grep -E '^sdk\.dir=' "$ANDROID_DIR/local.properties" | head -n1 | cut -d= -f2- | tr -d '\\' || true)"
    if [ -n "$SDK_FROM_PROP" ]; then
        ANDROID_SDK="$SDK_FROM_PROP"
    fi
fi
# 2) 环境变量
if [ -z "${ANDROID_SDK:-}" ] && [ -n "${ANDROID_HOME:-}" ] && [ -d "$ANDROID_HOME" ]; then
    ANDROID_SDK="$ANDROID_HOME"
fi
# 3) 常见位置（保留旧约定 ../android-sdk 作为兜底）
if [ -z "${ANDROID_SDK:-}" ]; then
    for cand in "$PROJECT_DIR/../android-sdk" "$HOME/WorkBuddy/Claw/android-sdk" "$HOME/AppData/Local/Android/Sdk"; do
        if [ -d "$cand" ]; then
            ANDROID_SDK="$cand"
            break
        fi
    done
fi
if [ -z "${ANDROID_SDK:-}" ] || [ ! -d "$ANDROID_SDK" ]; then
    echo "[ERROR] Android SDK 未找到。请设置 ANDROID_HOME 或创建 local.properties (sdk.dir=...)。" >&2
    exit 1
fi

export ANDROID_HOME="$ANDROID_SDK"
export ANDROID_SDK_ROOT="$ANDROID_SDK"

echo "== NovelGenerator Android Build =="
echo "ANDROID_HOME: $ANDROID_HOME"
echo ""

cd "$ANDROID_DIR"

# Check for Gradle wrapper, download if needed
if [ ! -f "gradlew" ]; then
    echo "Downloading Gradle wrapper..."
    gradle wrapper --gradle-version 8.9
fi

# Build debug APK
echo "Building debug APK..."
./gradlew assembleDebug

APK_PATH="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"
if [ -f "$APK_PATH" ]; then
    APK_SIZE=$(du -h "$APK_PATH" | cut -f1)
    echo ""
    echo "=== Build successful! ==="
    echo "APK: $APK_PATH"
    echo "Size: $APK_SIZE"
    echo ""
    echo "Install on device:"
    echo "  adb install $APK_PATH"
else
    echo "Build failed - APK not found"
    exit 1
fi
