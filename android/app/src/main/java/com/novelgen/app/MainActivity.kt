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
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.novelgen.app.databinding.ActivityMainBinding
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val handler = Handler(Looper.getMainLooper())
    private var retryCount = 0
    private var pollRunnable: Runnable? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)

        setupWebView()
        setupSwipeRefresh()
        startLocalServer()
    }

    private fun startLocalServer() {
        val prefs = getSharedPreferences("novelgen", MODE_PRIVATE)
        val apiKey = prefs.getString("api_key", "") ?: ""

        if (apiKey.isBlank()) {
            setStatus("请设置 API Key", R.color.error, R.drawable.dot_red)
            startActivity(Intent(this, ApiKeyActivity::class.java))
            return
        }

        setStatus("启动服务器...", R.color.warning, R.drawable.dot_yellow)

        // Start the service
        val intent = Intent(this, ServerService::class.java).apply {
            putExtra("api_key", apiKey)
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }

        // Poll status file directly — no broadcast dependency
        val statusFile = File(filesDir, "novelgen_status.txt")
        pollRunnable = object : Runnable {
            override fun run() {
                try {
                    val text = if (statusFile.exists()) statusFile.readText().trim() else ""
                    when {
                        text.startsWith("server_ready") -> {
                            setStatus("已连接", R.color.success, R.drawable.dot_green)
                            loadLocalFrontend()
                            return  // stop polling
                        }
                        text.startsWith("error_") -> {
                            val err = text.removePrefix("error_").take(80)
                            setStatus("错误: $err", R.color.error, R.drawable.dot_red)
                            binding.offlineBanner.text = err
                            binding.offlineBanner.visibility = View.VISIBLE
                            return
                        }
                        text.isNotBlank() -> {
                            // Show progress step
                            val label = text.replace("import_", "加载").replace("_", " ")
                                    .replace("init", "初始化").replace("engine ok", "引擎就绪")
                                    .replace("app ok", "服务就绪")
                            setStatus(label, R.color.warning, R.drawable.dot_yellow)
                        }
                    }
                } catch (_: Exception) {}
                handler.postDelayed(this, 500)
            }
        }
        handler.post(pollRunnable!!)
    }

    private fun setStatus(text: String, colorRes: Int, dotRes: Int) {
        binding.connectionStatus.text = text
        binding.connectionStatus.setTextColor(getColor(colorRes))
        binding.connectionDot.setBackgroundResource(dotRes)
    }

    private fun loadLocalFrontend() {
        handler.removeCallbacks(pollRunnable ?: return)
        binding.offlineBanner.visibility = View.GONE
        binding.webView.loadUrl("http://127.0.0.1:8899/")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val webView = binding.webView
        webView.settings.apply {
            javaScriptEnabled = true; domStorageEnabled = true
            allowFileAccess = false; allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            useWideViewPort = true; loadWithOverviewMode = true
        }
        webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                binding.progressBar.visibility = View.GONE
            }
            override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                if (req?.isForMainFrame == true && retryCount < 20) {
                    retryCount++
                    handler.postDelayed({ binding.webView.reload() }, 500)
                }
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, p: Int) { binding.progressBar.progress = p }
        }
    }

    private fun setupSwipeRefresh() {
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

    override fun onDestroy() {
        handler.removeCallbacks(pollRunnable ?: return)
        super.onDestroy()
    }
}
