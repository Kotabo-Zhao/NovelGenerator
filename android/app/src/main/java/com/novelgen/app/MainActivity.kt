package com.novelgen.app

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.View
import android.webkit.*
import androidx.appcompat.app.AppCompatActivity
import com.novelgen.app.databinding.ActivityMainBinding
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val handler = Handler(Looper.getMainLooper())
    private var frontendLoaded = false
    private var serverStartRequested = false
    private var mainFrameErrorCount = 0  // v2.5.41: 主 frame 加载错误重试上限（防无限 reload 闪烁）

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setupWebView()
        startLocalServer()
    }

    override fun onResume() {
        super.onResume()
        if (::binding.isInitialized) {
            val configured = getSharedPreferences("novelgen", MODE_PRIVATE)
                .getString("api_key", "")?.isNotBlank() == true
            if (!frontendLoaded && !serverStartRequested
                && (BuildConfig.INTERNAL_BUILD || configured)) {
                startLocalServer()
            }
        }
    }

    private fun startLocalServer() {
        val apiKey = getSharedPreferences("novelgen", MODE_PRIVATE).getString("api_key", "") ?: ""
        if (apiKey.isBlank() && !BuildConfig.INTERNAL_BUILD) {
            setStatus("请设置 API Key", R.color.error, R.drawable.dot_red)
            startActivity(Intent(this, ApiKeyActivity::class.java))
            return
        }
        if (serverStartRequested) return
        serverStartRequested = true
        val startupText = if (apiKey.isBlank()) "启动内部调试服务..." else "启动服务器..."
        setStatus(startupText, R.color.warning, R.drawable.dot_yellow)

        val intent = Intent(this, ServerService::class.java).apply {
            putExtra("api_key", apiKey)
            putExtra("internal_build", BuildConfig.INTERNAL_BUILD)
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O)
            startForegroundService(intent)
        else startService(intent)

        pollStatus()
    }

    private fun pollStatus() {
        if (frontendLoaded) return
        val raw = try {
            val f = File(filesDir, "novelgen_status.txt")
            if (f.exists()) f.readText().trim() else ""
        } catch (_: Exception) { "" }
        // Strip timestamp: "1784984622|server_ready" → "server_ready"
        val text = raw.substringAfter("|", raw)

        when {
            text.startsWith("server_ready") -> {
                setStatus("已连接", R.color.success, R.drawable.dot_green)
                binding.offlineBanner.visibility = View.GONE
                binding.progressBar.visibility = View.VISIBLE
                binding.webView.loadUrl("http://127.0.0.1:8899/")
                frontendLoaded = true
                return
            }
            text.startsWith("error_") -> {
                setStatus("错误: ${text.removePrefix("error_").take(80)}", R.color.error, R.drawable.dot_red)
                binding.offlineBanner.text = text.removePrefix("error_").take(200)
                binding.offlineBanner.visibility = View.VISIBLE
                return
            }
            text.isNotBlank() -> {
                val label = text.replace("import_", "加载").replace("_", " ")
                    .replace("init", "初始化")
                setStatus(label, R.color.warning, R.drawable.dot_yellow)
            }
        }
        handler.postDelayed({ pollStatus() }, 500)
    }

    private fun setStatus(text: String, colorRes: Int, dotRes: Int) {
        binding.connectionStatus.text = text
        binding.connectionStatus.setTextColor(getColor(colorRes))
        binding.connectionDot.setBackgroundResource(dotRes)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        binding.webView.settings.apply {
            javaScriptEnabled = true; domStorageEnabled = true
            allowFileAccess = false; allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            useWideViewPort = true; loadWithOverviewMode = true
            // v2.5.15: 禁缓存——升级 APK 后 WebView 必加载新前端（否则缓存旧页面
            // 导致功能"没生效"的假象）
            cacheMode = WebSettings.LOAD_NO_CACHE
        }
        binding.webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")

        binding.webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                binding.progressBar.visibility = View.GONE
                binding.swipeRefresh.isRefreshing = false  // v2.27: 修复下拉刷新动画停不下来
                // v2.5.38: 彻底禁用 Service Worker——SW 缓存独立于 HTTP 缓存，
                // LOAD_NO_CACHE 管不到它（覆盖安装仍可能用旧页面）
                // v2.5.41: 防死循环——sessionStorage 标记同 tab reload 保留，
                // 一次启动只清理一次；App 冷启动清空 → 每次启动仍清理一次旧 SW。
                // （否则 unregister→reload→页面重注册 SW→再 reload 无限循环闪烁）
                binding.webView.evaluateJavascript(
                    "if ('serviceWorker' in navigator && !sessionStorage.getItem('__ng_sw_cleaned')) {" +
                    "sessionStorage.setItem('__ng_sw_cleaned', '1');" +
                    "navigator.serviceWorker.getRegistrations().then(rs => {" +
                    "if (rs.length) { Promise.all(rs.map(r => r.unregister())).then(() => {" +
                    "caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k))))" +
                    ".then(() => { if (location.pathname === '/') location.reload(); })" +
                    "}); }" +
                    "});" +
                    "}", null)
            }
            override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                binding.swipeRefresh.isRefreshing = false  // 错误时也停止刷新动画
                if (req?.isForMainFrame == true) {
                    // v2.5.41: 最多重试 3 次，防止后端暂不可用时无限 reload 闪烁
                    mainFrameErrorCount++
                    if (mainFrameErrorCount <= 3) {
                        handler.postDelayed({ binding.webView.reload() }, 1000)
                    }
                }
            }
        }
        binding.webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, p: Int) { binding.progressBar.progress = p }
        }

        binding.swipeRefresh.setOnRefreshListener { binding.webView.reload() }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && binding.webView.canGoBack()) {
            binding.webView.goBack(); return true
        }
        return super.onKeyDown(keyCode, event)
    }
}
