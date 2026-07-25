package com.novelgen.app

import android.annotation.SuppressLint
import android.content.*
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
import kotlinx.coroutines.*

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val handler = Handler(Looper.getMainLooper())
    private var retryCount = 0
    private var statusReceiver: BroadcastReceiver? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)

        setupWebView()
        setupSwipeRefresh()
        registerStatusReceiver()

        // Start embedded Python server
        startLocalServer()
    }

    private fun startLocalServer() {
        binding.connectionStatus.text = "启动服务器..."
        binding.connectionStatus.setTextColor(getColor(R.color.warning))

        val prefs = getSharedPreferences("novelgen", MODE_PRIVATE)
        val apiKey = prefs.getString("api_key", "") ?: ""

        if (apiKey.isBlank()) {
            // No API key — show config
            binding.connectionStatus.text = "请设置 API Key"
            binding.connectionStatus.setTextColor(getColor(R.color.error))
            startActivity(Intent(this, ApiKeyActivity::class.java))
            return
        }

        val intent = Intent(this, ServerService::class.java).apply {
            putExtra("api_key", apiKey)
            putExtra("host", "127.0.0.1")
            putExtra("port", 8899)
        }

        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    private fun registerStatusReceiver() {
        statusReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                val ready = intent?.getBooleanExtra("ready", false) ?: false
                val error = intent?.getStringExtra("error")

                if (ready) {
                    binding.connectionStatus.text = getString(R.string.connection_success)
                    binding.connectionStatus.setTextColor(getColor(R.color.success))
                    binding.connectionDot.setBackgroundResource(R.drawable.dot_green)
                    binding.offlineBanner.visibility = View.GONE
                    // Load frontend
                    handler.postDelayed({ loadLocalFrontend() }, 500)
                } else if (error != null) {
                    binding.connectionStatus.text = "错误: ${error.take(40)}"
                    binding.connectionStatus.setTextColor(getColor(R.color.error))
                    binding.connectionDot.setBackgroundResource(R.drawable.dot_red)
                }
            }
        }
        registerReceiver(statusReceiver, IntentFilter(ServerService.BROADCAST_STATUS),
            RECEIVER_NOT_EXPORTED)
    }

    private fun loadLocalFrontend() {
        binding.webView.loadUrl("http://127.0.0.1:8899/")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val webView = binding.webView

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            useWideViewPort = true
            loadWithOverviewMode = true
            setSupportZoom(true)
            builtInZoomControls = true
            displayZoomControls = false
        }

        webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                binding.progressBar.visibility = View.GONE
                binding.swipeRefresh.isRefreshing = false
            }

            override fun onReceivedError(
                view: WebView?, request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true && retryCount < 5) {
                    retryCount++
                    handler.postDelayed({ binding.webView.reload() }, 1000)
                }
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                if (!url.startsWith("http://127.0.0.1")) {
                    val intent = Intent(Intent.ACTION_VIEW, request?.url)
                    startActivity(intent)
                    return true
                }
                return false
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                binding.progressBar.progress = newProgress
            }
            override fun onReceivedTitle(view: WebView?, title: String?) {
                supportActionBar?.title = title ?: getString(R.string.app_name)
            }
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
        menuInflater.inflate(R.menu.main_menu, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.menu_refresh -> { binding.webView.reload(); true }
            R.id.menu_config -> {
                startActivity(Intent(this, ApiKeyActivity::class.java)); true
            }
            R.id.menu_about -> {
                AlertDialog.Builder(this).setTitle("关于")
                    .setMessage("小说工坊 v1.1\n前后端一体化运行\nPython + Kotlin + Chaquopy")
                    .setPositiveButton("确定", null).show()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }

    override fun onDestroy() {
        unregisterReceiver(statusReceiver)
        super.onDestroy()
    }
}
