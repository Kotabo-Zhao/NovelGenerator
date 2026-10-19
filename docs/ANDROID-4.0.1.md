# Android 4.0.1

移除应用顶部自动显示「墨局」的原生 Toolbar，以及占用页面高度的常驻连接状态条。启动提示和进度只在载入期间显示，完成后页面直接使用整个内容区域；模型连接继续使用页面内的设置入口。

[下载安装包](https://github.com/Kotabo-Zhao/NovelGenerator/releases/download/v4.0.1/NovelGenerator-v4.0.1-arm64-debug.apk) · [Release 页面](https://github.com/Kotabo-Zhao/NovelGenerator/releases/tag/v4.0.1)

- 版本 4.0.1，versionCode 58，包名 `com.novelgen.app`。
- ARM64，Android 8.0+，调试签名与本地 2.5.68 / 4.0.0 一致，支持覆盖升级。
- 文件名 `NovelGenerator-v4.0.1-arm64-debug.apk`，31,324,835 字节。
- SHA256：`7ee68be4d7484a8ad5cf17c75dbc46f75e5160b12da8f2ad663d81d7eb5aae3a`。
- PDF / EPUB 继续暂缓，安卓只开放 TXT 导出。

Gradle 构建、APK v2 签名、版本/架构检查通过。包内 83 个 Python 文件和 14 个前端资源与当前源码匹配，编译后的主界面布局中不存在 Toolbar、常驻连接条或应用名称标题。检查记录在 `reports/android-4.0.1/`。

本次改动是安卓原生布局调整，复用 [4.0.0 的后端与协议验证](ANDROID-4.0.0.md)。没有连接真机；实际安装、升级、输入法、文件读写和后台行为仍未进行真机验收。
