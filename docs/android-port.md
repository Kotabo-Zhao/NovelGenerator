# NovelGenerator Android 移植

> 基于 PWA + 响应式 | android-port 分支

## 快速部署到 Android

### 方式一：PWA（推荐，零代码）

1. 确保 NovelGenerator 服务在 PC 上运行：`python -m uvicorn api.server:app --host 0.0.0.0 --port 8899`
2. 在 Android 手机 Chrome 浏览器打开 `http://<PC_IP>:8899`
3. Chrome 会自动弹出 "添加到主屏幕" 横幅 → 点击添加
4. 桌面出现 ✍️ 图标，点击即用（全屏独立窗口，像原生 App）

### 方式二：Android WebView 原生壳

1. 创建 Android 项目
2. 添加 WebView：
```kotlin
// MainActivity.kt
class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = true
            loadUrl("http://<PC_IP>:8899")
        }
        setContentView(webView)
    }
}
```
3. 添加网络权限 `AndroidManifest.xml`：
```xml
<uses-permission android:name="android.permission.INTERNET" />
```
4. 构建 APK → 安装

### 方式三：本地后端 + 内网穿透

如果不想 PC 一直开机：
1. 部署后端到 Render/Railway 等云平台
2. Android PWA 直接访问云端 URL

## 技术栈

| 层 | 技术 | 原因 |
|:---|:-----|:-----|
| 前端 | PWA (HTML+CSS+JS) | 零安装，离线缓存，全屏体验 |
| 后端 | Python FastAPI | 保持不变，LLM API 调用 |
| 移动适配 | CSS @media + viewport | 768px/480px 断点 |
| Service Worker | Cache API | 静态资源缓存 + API 离线降级 |

## 已完成的适配

- ✅ PWA Manifest (图标/主题色/全屏模式)
- ✅ Service Worker v5 (离线缓存 + API fallback)
- ✅ 响应式 CSS (手机/平板断点)
- ✅ PWA 安装提示横幅
- ✅ iOS safe-area 适配
- ✅ 表单 16px 字体（防 iOS 缩放）
- ✅ 触屏友好按钮尺寸

## 待完成

- [ ] Android 原生壳项目 (Kotlin WebView)
- [ ] 推送通知（章节完成提醒）
- [ ] 本地 SQLite 缓存已生成章节
- [ ] 后台生成（WorkManager）
- [ ] 分享/导出到其他 App
