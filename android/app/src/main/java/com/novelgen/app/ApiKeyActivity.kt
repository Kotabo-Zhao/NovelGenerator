package com.novelgen.app

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.novelgen.app.databinding.ActivityApiKeyBinding

class ApiKeyActivity : AppCompatActivity() {

    private lateinit var binding: ActivityApiKeyBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityApiKeyBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("novelgen", MODE_PRIVATE)
        binding.apiKeyInput.setText(prefs.getString("api_key", ""))

        binding.saveButton.setOnClickListener {
            val key = binding.apiKeyInput.text.toString().trim()
            if (key.isBlank()) {
                binding.apiKeyInput.error = "请输入 API Key"
                return@setOnClickListener
            }
            if (!key.startsWith("sk-")) {
                binding.apiKeyInput.error = "API Key 应以 sk- 开头"
                return@setOnClickListener
            }
            prefs.edit().putString("api_key", key).apply()
            val serviceIntent = android.content.Intent(this, ServerService::class.java).apply {
                putExtra("api_key", key)
                putExtra("internal_build", BuildConfig.INTERNAL_BUILD)
                putExtra("update_only", true)
            }
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
            setResult(RESULT_OK)
            Toast.makeText(this, "API Key 已保存，当前服务已刷新", Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}
