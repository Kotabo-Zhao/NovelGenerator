# NovelGenerator Android 原生移植

> `android-port` 分支 — 不影响 `main` 分支

## 架构

```
┌─────────────────────────────────┐
│        Android APK              │
│  ┌───────────────────────────┐  │
│  │  SplashActivity           │  │
│  │   → ServerConfigActivity  │  │  (首次配置 IP:端口)
│  │   → MainActivity          │  │
│  │      ┌────────────────┐   │  │
│  │      │   WebView       │   │  │  ← 加载前端
│  │      │   + JS Bridge   │   │  │  ← 原生能力桥接
│  │      └────────────────┘   │  │
│  └───────────────────────────┘  │
│              │ HTTP             │
└──────────────┼──────────────────┘
               │
┌──────────────┼──────────────────┐
│         PC / 服务器              │
│  ┌───────────────────────────┐  │
│  │  uvicorn api.server:app   │  │
│  │  :8899                    │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

## 原生能力

| 能力 | 实现 |
|:-----|:-----|
| APK 安装 | 标准 Android 包 |
| 桌面图标 | 自适应图标 (Adaptive Icon) |
| 启动画面 | SplashActivity (1.5s) |
| 服务器配置 | ServerConfigActivity，保存到 SharedPreferences |
| 连接状态 | 顶部状态栏，实时绿/黄/红指示 |
| 文件下载 | Android DownloadManager |
| 内容分享 | Intent.ACTION_SEND |
| 本地存储 | SharedPreferences (JS 可通过 AndroidBridge 读写) |
| 下拉刷新 | SwipeRefreshLayout |
| 返回键 | WebView 导航栈 |
| 离线提示 | 断网横幅 + 点击重试 |
| 原生通知 | 预留 NotificationHelper |

## JS Bridge API

前端通过 `window.AndroidBridge` 调用：

```javascript
// 下载文件
AndroidBridge.downloadFile("http://...", "novel.txt");

// 分享内容
AndroidBridge.shareContent("标题", "内容");

// 显示 Toast
AndroidBridge.toast("操作成功");

// 持久化存储
AndroidBridge.savePreference("key", "value");
const val = AndroidBridge.loadPreference("key");
```

## 构建

```bash
# 前置条件
# - Android SDK (api 34/35) 在 ../android-sdk/
# - JDK 17+
# - Gradle 8.9

cd android
./build.sh
# APK → app/build/outputs/apk/debug/app-debug.apk
```

或手动：
```bash
export ANDROID_HOME=../android-sdk
cd android && ./gradlew assembleDebug
```

## 使用流程

1. PC 启动后端：
   ```bash
   cd NovelGenerator/backend
   python -m uvicorn api.server:app --host 0.0.0.0 --port 8899
   ```

2. Android 安装 APK → 打开 App

3. 首次输入 PC 的局域网 IP（如 `192.168.1.100:8899`）

4. 测试连接 → 成功后自动进入主界面

5. 后续打开直接连接，无需重新配置

## 分支管理

- `main` — 主线，不受影响
- `android-port` — Android 移植分支

Android 移植改动不触及 `backend/` 核心代码。
