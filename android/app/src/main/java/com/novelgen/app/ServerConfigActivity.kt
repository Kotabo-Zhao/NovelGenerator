package com.novelgen.app

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.novelgen.app.databinding.ActivityServerConfigBinding
import kotlinx.coroutines.*

class ServerConfigActivity : AppCompatActivity() {

    private lateinit var binding: ActivityServerConfigBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityServerConfigBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("novelgen", MODE_PRIVATE)
        binding.serverInput.setText(prefs.getString("server_url", "192.168.1.100:8899"))

        binding.connectButton.setOnClickListener {
            val url = binding.serverInput.text.toString().trim()
            if (url.isBlank()) {
                binding.serverInput.error = "请输入服务器地址"
                return@setOnClickListener
            }

            binding.connectButton.isEnabled = false
            binding.connectButton.text = getString(R.string.connecting)
            binding.progressBar.visibility = android.view.View.VISIBLE

            // Test connection in background
            CoroutineScope(Dispatchers.IO).launch {
                val ok = testConnection(url)
                withContext(Dispatchers.Main) {
                    binding.connectButton.isEnabled = true
                    binding.connectButton.text = getString(R.string.connect_button)
                    binding.progressBar.visibility = android.view.View.GONE

                    if (ok) {
                        prefs.edit().putString("server_url", url).apply()
                        Toast.makeText(this@ServerConfigActivity, R.string.connection_success, Toast.LENGTH_SHORT).show()
                        val intent = Intent(this@ServerConfigActivity, MainActivity::class.java)
                        intent.putExtra("server_url", url)
                        startActivity(intent)
                        finish()
                    } else {
                        Toast.makeText(this@ServerConfigActivity, R.string.connection_failed, Toast.LENGTH_LONG).show()
                    }
                }
            }
        }

        // Quick: skip config if already saved
        val saved = prefs.getString("server_url", "")
        if (!saved.isNullOrBlank()) {
            binding.skipButton.setOnClickListener {
                val intent = Intent(this, MainActivity::class.java)
                intent.putExtra("server_url", saved)
                startActivity(intent)
                finish()
            }
        }
    }

    private fun testConnection(url: String): Boolean {
        return try {
            val conn = java.net.URL("http://$url/api/health").openConnection() as java.net.HttpURLConnection
            conn.connectTimeout = 3000
            conn.readTimeout = 3000
            conn.requestMethod = "GET"
            conn.responseCode == 200
        } catch (e: Exception) {
            false
        }
    }
}
