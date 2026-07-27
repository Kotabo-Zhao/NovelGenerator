package com.novelgen.app

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.*
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.novelgen.app.databinding.ActivityMainBinding
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val handler = Handler(Looper.getMainLooper())
    private var frontendLoaded = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        setupWebView()
        startLocalServer()
    }

    private fun startLocalServer() {
        val apiKey = getSharedPreferences("novelgen", MODE_PRIVATE).getString("api_key", "") ?: ""
        if (apiKey.isBlank()) {
            setStatus("请设置 API Key", R.color.error, R.drawable.dot_red)
            startActivity(Intent(this, ApiKeyActivity::class.java))
            return
        }
        setStatus("启动服务器...", R.color.warning, R.drawable.dot_yellow)

        val intent = Intent(this, ServerService::class.java).apply { putExtra("api_key", apiKey) }
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
        }
        binding.webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")

        binding.webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                binding.progressBar.visibility = View.GONE
                binding.swipeRefresh.isRefreshing = false  // v2.27: 修复下拉刷新动画停不下来
            }
            override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                binding.swipeRefresh.isRefreshing = false  // 错误时也停止刷新动画
                if (req?.isForMainFrame == true) {
                    handler.postDelayed({ binding.webView.reload() }, 1000)
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

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main_menu, menu); return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        R.id.menu_refresh -> { binding.webView.reload(); true }
        R.id.menu_config -> { startActivity(Intent(this, ApiKeyActivity::class.java)); true }
        R.id.menu_about -> {
            AlertDialog.Builder(this).setTitle("关于")
                .setMessage("小说工坊 v1.1\n前后端一体化 · Chaquopy + FastAPI")
                .setPositiveButton("确定", null).show(); true
        }
        else -> super.onOptionsItemSelected(item)
    }
}
