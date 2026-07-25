package com.novelgen.app

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.appcompat.app.AppCompatActivity

class SplashActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_splash)

        Handler(Looper.getMainLooper()).postDelayed({
            val prefs = getSharedPreferences("novelgen", MODE_PRIVATE)
            val serverUrl = prefs.getString("server_url", "")

            if (serverUrl.isNullOrBlank()) {
                startActivity(Intent(this, ServerConfigActivity::class.java))
            } else {
                val intent = Intent(this, MainActivity::class.java)
                intent.putExtra("server_url", serverUrl)
                startActivity(intent)
            }
            finish()
        }, 1500)
    }
}
