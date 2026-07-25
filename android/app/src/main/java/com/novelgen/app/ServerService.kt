package com.novelgen.app

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class ServerService : Service() {

    companion object {
        const val CHANNEL_ID = "novelgen_server"
        const val NOTIFICATION_ID = 1001
        const val ACTION_START = "com.novelgen.app.START_SERVER"
        const val BROADCAST_STATUS = "com.novelgen.app.SERVER_STATUS"

        var isServerRunning = false
        var isServerReady = false
        var serverError: String? = null
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val apiKey = intent?.getStringExtra("api_key") ?: ""
        val host = intent?.getStringExtra("host") ?: "127.0.0.1"
        val port = intent?.getIntExtra("port", 8899) ?: 8899

        startForeground(NOTIFICATION_ID, buildNotification("正在启动服务器..."))

        Thread {
            try {
                // Initialize Python
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(this))
                }

                val py = Python.getInstance()
                val module = py.getModule("server_runner")

                // Start the server
                module.callAttr("start_server", apiKey, host, port)
                isServerRunning = true

                // Poll for readiness
                var waited = 0
                while (waited < 30) {
                    Thread.sleep(500)
                    waited++
                    val pyObj = module.callAttr("get_server_status")
                    val ready = pyObj.get("ready")?.toBoolean() ?: false
                    val err = pyObj.get("error")?.toString()

                    if (err != null) {
                        serverError = err
                        broadcastStatus(false, err)
                        updateNotification("服务器错误: ${err.take(60)}")
                        return@Thread
                    }

                    if (ready) {
                        isServerReady = true
                        broadcastStatus(true, null)
                        updateNotification("服务器运行中 — http://$host:$port")
                        return@Thread
                    }
                }

                // Timeout
                serverError = "服务器启动超时"
                broadcastStatus(false, "timeout")
                updateNotification("启动超时")

            } catch (e: Exception) {
                serverError = e.message ?: "未知错误"
                broadcastStatus(false, serverError!!)
                updateNotification("启动失败: ${serverError!!.take(60)}")
            }
        }.start()

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        isServerRunning = false
        isServerReady = false
        super.onDestroy()
    }

    private fun broadcastStatus(ready: Boolean, error: String?) {
        val intent = Intent(BROADCAST_STATUS).apply {
            putExtra("ready", ready)
            putExtra("error", error)
        }
        sendBroadcast(intent)
    }

    private fun buildNotification(text: String): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("小说工坊")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun updateNotification(text: String) {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "小说工坊服务",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "后台运行小说生成服务器"
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }
    }
}
