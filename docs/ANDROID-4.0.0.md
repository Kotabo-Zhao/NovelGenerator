# 墨局 Android 4.0.0 交付记录

安装包位于 `dist/NovelGenerator-v4.0.0-arm64-debug.apk`，大小 **31,342,117 字节**（约 30 MiB）。

| 项目 | 结果 |
| --- | --- |
| 应用名 / 包名 | 墨局 / `com.novelgen.app` |
| 版本 | 4.0.0，versionCode 57 |
| 系统 / 架构 | Android 8.0+，ARM64 |
| SDK | minSdk 26 / targetSdk 35 |
| 签名 | Android Debug，APK v2 校验通过 |
| 与本地旧包 2.5.68 签名 | 相同，支持覆盖升级 |
| SHA256 | `2ff4b6be7121a5515248c932773446a510453cd6e6d04fee34f21cb9d3eae7bf` |

保留旧 APK。包名、数据目录和签名保持兼容，升级沿用应用内部 `files/novels`、`files/_roleplay` 和连接设置。首次使用不再被旧 API Key 页面拦住；旧版偏好中的 Key 仍可用于首次初始化，新连接设置保存在应用私有目录。

## 安卓适配

- 包含新版书架、创作工作台、角色扮演与流式正文修复；包内没有旧 `core/interactive` 或旧互动路由。
- 系统文件选择器接入 WebView，供 PNG/JSON 角色卡和头像导入使用。
- TXT、JSON 角色卡和聊天导出接入原生保存：Android 10+ 写入「下载/墨局」，Android 8/9 使用带临时读取权限的系统分享。
- **按用户当前要求，安卓 PDF / EPUB 暂缓**。界面隐藏入口，接口仅开放 TXT，未打入相关 PDF/XML 二进制依赖。
- 原生菜单与页面使用同一模型连接弹窗，修改后可即时生效。
- 适配系统栏与输入法边距；停用会打断聊天的整页下拉刷新，原生刷新菜单仍可使用。
- 本地服务防止重复启动，真正监听端口后才报告就绪，失败和超时可见。

## 验证与证据

- Gradle `:app:assembleDebug`：**BUILD SUCCESSFUL**。
- `apksigner verify --verbose --print-certs`：通过，并对比旧包证书相同。
- `aapt2 dump badging`：确认版本、包名、最低系统与 ARM64 架构。
- `tools/verify_android_apk.py`（Python 3.13）：**83 个内嵌 Python 字节码与当前源码匹配，14 个前端资源与唯一源目录逐字节匹配**；核对旧互动代码、应用数据与配置文件未打包。
- `tools/verify_android_backend.py`：Windows 主机运行安卓源码，使用与 APK 相同的 Python 3.13 / FastAPI 0.125.0 / Pydantic 1.10.26 / OpenAI 1.39.0 / HTTPX 0.27.2，**7 项通过**。覆盖无 Key 启动、静态资源、连接修改、SDK 本地 HTTP 调用、中文/emoji 流式与落盘、移除旧模式、暂缓导出与重复启动。
- PC 后端回归 **142 passed**，前端协议/安全渲染/安卓导出桥回归 **24 passed**；Ruff F821/F811、双副本同步与差异空白检查通过。

机器记录在 `reports/android-4.0.0/`：`build.log`、`signature-new.txt`、`signature-old.txt`、`apk-badging.txt`、`backend-compatibility.json` 与 `receipt.json`。校验和另存为 APK 同目录的 `.apk.sha256` 文件。

## 未验收范围

`adb devices -l` 没有连接设备。**真机安装/升级、原生文件选择与保存、系统键盘、前后台切换、真实模型生成、长时间使用均为 NOT_RUN**。主机协议检查与 APK 静态校验不代表这些项目已通过。

本机复现构建：设置 `JAVA_HOME=C:\dev\jdk-17`、`ANDROID_HOME=C:\dev\android-sdk`、`CHAQUOPY_BUILD_PYTHON=C:\dev\python-3.13\python.exe`，在 `android/` 执行 `gradlew.bat :app:assembleDebug --console=plain`。构建前先运行两个同步脚本。
