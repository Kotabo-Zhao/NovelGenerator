package com.novelgen.app

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Bundle
import android.view.KeyEvent
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.*
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.novelgen.app.databinding.ActivityMainBinding
import kotlinx.coroutines.*

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var serverUrl: String = ""
    private var isConnected: Boolean = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)

        serverUrl = intent.getStringExtra("server_url") ?: run {
            val saved = getSharedPreferences("novelgen", MODE_PRIVATE).getString("server_url", "")
            saved ?: "192.168.1.100:8899"
        }

        setupWebView()
        setupSwipeRefresh()

        if (isNetworkAvailable()) {
            loadNovelGenerator()
        } else {
            showOfflineBanner()
        }
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
            // Performance
            setRenderPriority(WebSettings.RenderPriority.HIGH)
        }

        // Enable WebView debugging in debug builds
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.KITKAT) {
            WebView.setWebContentsDebuggingEnabled(false)
        }

        webView.addJavascriptInterface(WebAppInterface(this), "AndroidBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                binding.progressBar.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                binding.progressBar.visibility = View.GONE
                binding.swipeRefresh.isRefreshing = false
                if (!isConnected && url?.contains("/api/health") != true) {
                    isConnected = true
                    updateConnectionStatus(true)
                }
            }

            override fun onReceivedError(
                view: WebView?, request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true) {
                    isConnected = false
                    updateConnectionStatus(false)
                }
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                // Handle download links
                if (url.contains("/api/novels/") && (url.contains("/export") || url.contains("/pdf"))) {
                    WebAppInterface(this@MainActivity).downloadFile(url, "novel_export")
                    return true
                }
                // Open external links in browser
                if (!url.startsWith("http://$serverUrl") && !url.startsWith("https://")) {
                    val intent = android.content.Intent(android.content.Intent.ACTION_VIEW, request?.url)
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
        binding.swipeRefresh.setColorSchemeResources(
            android.R.color.holo_blue_bright,
            android.R.color.holo_purple,
            android.R.color.holo_orange_light
        )
        binding.swipeRefresh.setOnRefreshListener {
            binding.webView.reload()
        }
    }

    private fun loadNovelGenerator() {
        binding.connectionStatus.text = getString(R.string.connecting)
        binding.connectionStatus.setTextColor(getColor(R.color.warning))

        CoroutineScope(Dispatchers.IO).launch {
            // Quick health check
            val available = try {
                val conn = java.net.URL("http://$serverUrl/api/health").openConnection() as java.net.HttpURLConnection
                conn.connectTimeout = 3000
                conn.readTimeout = 3000
                conn.responseCode == 200
            } catch (e: Exception) { false }

            withContext(Dispatchers.Main) {
                if (available) {
                    isConnected = true
                    updateConnectionStatus(true)
                    binding.webView.loadUrl("http://$serverUrl/")
                } else {
                    isConnected = false
                    updateConnectionStatus(false)
                    showOfflineBanner()
                }
            }
        }
    }

    private fun updateConnectionStatus(connected: Boolean) {
        if (connected) {
            binding.connectionStatus.text = getString(R.string.connection_success)
            binding.connectionStatus.setTextColor(getColor(R.color.success))
            binding.connectionDot.setBackgroundResource(R.drawable.dot_green)
            binding.offlineBanner.visibility = View.GONE
        } else {
            binding.connectionStatus.text = getString(R.string.no_connection)
            binding.connectionStatus.setTextColor(getColor(R.color.error))
            binding.connectionDot.setBackgroundResource(R.drawable.dot_red)
        }
    }

    private fun showOfflineBanner() {
        binding.offlineBanner.visibility = View.VISIBLE
        binding.offlineBanner.setOnClickListener {
            binding.offlineBanner.visibility = View.GONE
            loadNovelGenerator()
        }
    }

    private fun isNetworkAvailable(): Boolean {
        val cm = getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && binding.webView.canGoBack()) {
            binding.webView.goBack()
            return true
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
                startActivity(android.content.Intent(this, ServerConfigActivity::class.java))
                true
            }
            R.id.menu_about -> {
                AlertDialog.Builder(this)
                    .setTitle("关于")
                    .setMessage(R.string.about_text)
                    .setPositiveButton("确定", null)
                    .show()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }
}
