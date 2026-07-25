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
            Toast.makeText(this, "已保存，重启生效", Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}
