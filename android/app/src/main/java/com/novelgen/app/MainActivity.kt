package com.novelgen.app

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.View
import android.webkit.*
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.novelgen.app.databinding.ActivityMainBinding
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val handler = Handler(Looper.getMainLooper())
    private var frontendLoaded = false
    private var serverStartRequested = false
    private var mainFrameErrorCount = 0  // v2.5.41: 主 frame 加载错误重试上限（防无限 reload 闪烁）
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var startupDeadline = 0L
    private val chooseFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
        fileCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, keyboard.bottom))
            WindowInsetsCompat.CONSUMED
        }
        binding.offlineBanner.setOnClickListener {
            frontendLoaded = false
            mainFrameErrorCount = 0
            startLocalServer()
        }
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
        setStatus("正在启动本地服务…", R.color.warning)
        startupDeadline = android.os.SystemClock.elapsedRealtime() + 90000

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
        if (android.os.SystemClock.elapsedRealtime() > startupDeadline) {
            setStatus("启动超时", R.color.error)
            binding.offlineBanner.text = "本地服务启动超时，点击重试"
            binding.offlineBanner.visibility = View.VISIBLE
            return
        }
        val raw = try {
            val f = File(filesDir, "novelgen_status.txt")
            if (f.exists()) f.readText().trim() else ""
        } catch (_: Exception) { "" }
        // Strip timestamp: "1784984622|server_ready" → "server_ready"
        val text = raw.substringAfter("|", raw)

        when {
            text.startsWith("server_ready") -> {
                setStatus("正在打开工作台…", R.color.success)
                binding.offlineBanner.visibility = View.GONE
                binding.progressBar.visibility = View.VISIBLE
                binding.webView.loadUrl("http://127.0.0.1:8899/")
                frontendLoaded = true
                return
            }
            text.startsWith("error_") -> {
                setStatus("本地服务启动失败", R.color.error)
                binding.offlineBanner.text = text.removePrefix("error_").take(200)
                binding.offlineBanner.visibility = View.VISIBLE
                return
            }
            text.isNotBlank() -> {
                val label = text.replace("import_", "加载").replace("_", " ")
                    .replace("init", "初始化")
                setStatus(label, R.color.warning)
            }
        }
        handler.postDelayed({ pollStatus() }, 500)
    }

    private fun setStatus(text: String, colorRes: Int) {
        binding.startupStatus.visibility = View.VISIBLE
        binding.startupStatus.text = text
        binding.startupStatus.setTextColor(getColor(colorRes))
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        binding.webView.settings.apply {
            javaScriptEnabled = true; domStorageEnabled = true
            allowFileAccess = false; allowContentAccess = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            useWideViewPort = true; loadWithOverviewMode = true
            // v2.5.15: 禁缓存——升级 APK 后 WebView 必加载新前端（否则缓存旧页面
            // 导致功能"没生效"的假象）
            cacheMode = WebSettings.LOAD_NO_CACHE
        }
        binding.webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")

        binding.webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val uri = request?.url ?: return false
                if (uri.scheme == "http" && uri.host == "127.0.0.1" && uri.port == 8899) return false
                if (uri.scheme == "https" || uri.scheme == "http") {
                    try { startActivity(Intent(Intent.ACTION_VIEW, uri)) } catch (_: Exception) { }
                }
                return true
            }
            override fun onPageFinished(view: WebView?, url: String?) {
                mainFrameErrorCount = 0
                binding.startupStatus.visibility = View.GONE
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
            override fun onShowFileChooser(view: WebView?, callback: ValueCallback<Array<Uri>>?, params: FileChooserParams?): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    chooseFile.launch(params?.createIntent() ?: Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                        type = "*/*"
                        addCategory(Intent.CATEGORY_OPENABLE)
                    })
                    true
                } catch (_: Exception) {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = null
                    false
                }
            }
        }

        // The app has its own scrollable chat panes; pull-to-refresh can cancel a reply.
        binding.swipeRefresh.isEnabled = false
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && binding.webView.canGoBack()) {
            binding.webView.goBack(); return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        fileCallback?.onReceiveValue(null)
        fileCallback = null
        binding.webView.removeJavascriptInterface("AndroidBridge")
        binding.webView.destroy()
        super.onDestroy()
    }
}
