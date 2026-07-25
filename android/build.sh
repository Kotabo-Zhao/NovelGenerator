#!/bin/bash
# Build NovelGenerator Android APK
# Prerequisites: Android SDK at ../android-sdk, JDK 17+

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ANDROID_SDK="$PROJECT_DIR/../android-sdk"
ANDROID_DIR="$PROJECT_DIR/android"

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
