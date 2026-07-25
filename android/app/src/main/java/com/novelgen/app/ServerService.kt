package com.novelgen.app

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

class ServerService : Service() {

    companion object {
        const val CHANNEL_ID = "novelgen_server"
        const val NOTIFICATION_ID = 1001
        const val BROADCAST_STATUS = "com.novelgen.app.SERVER_STATUS"
        var isServerReady = false
        var lastStatus = ""
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(NotificationChannel(
                    CHANNEL_ID, "小说工坊服务", NotificationManager.IMPORTANCE_LOW))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val apiKey = intent?.getStringExtra("api_key") ?: ""
        val logDir = filesDir.absolutePath  // app-private, no permission needed
        startForeground(NOTIFICATION_ID, buildNotification("初始化 Python..."))

        Thread {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                val module = Python.getInstance().getModule("server_runner")

                // Start async — returns immediately
                updateNotification("加载模块...")
                module.callAttr("start_server", apiKey, "127.0.0.1", 8899, logDir)
                lastStatus = "started"

                // Poll status file
                val statusFile = File(logDir, "novelgen_status.txt")
                var waited = 0
                while (waited < 180) {  // 90 seconds
                    Thread.sleep(500)
                    waited++
                    try {
                        val text = if (statusFile.exists()) statusFile.readText().trim() else "waiting"
                        if (text != lastStatus) {
                            lastStatus = text
                            updateNotification(text.take(50))
                        }
                        if (text.startsWith("server_ready")) {
                            isServerReady = true
                            broadcastStatus(true, null)
                            updateNotification("运行中 — 127.0.0.1:8899")
                            return@Thread
                        }
                        if (text.startsWith("error_")) {
                            val err = text.removePrefix("error_")
                            broadcastStatus(false, err)
                            updateNotification("失败: ${err.take(80)}") 
                            return@Thread
                        }
                    } catch (_: Exception) {}
                }

                broadcastStatus(false, "超时 — 最后状态: $lastStatus")
                updateNotification("超时: $lastStatus")

            } catch (e: Exception) {
                broadcastStatus(false, e.message ?: "error")
                updateNotification("异常: ${e.message?.take(80)}")
            }
        }.start()

        return START_STICKY
    }

    private fun broadcastStatus(ready: Boolean, error: String?) {
        sendBroadcast(Intent(BROADCAST_STATUS).apply {
            putExtra("ready", ready); putExtra("error", error)
        })
    }

    private fun buildNotification(text: String) = NotificationCompat.Builder(this, CHANNEL_ID)
        .setContentTitle("小说工坊").setContentText(text)
        .setSmallIcon(android.R.drawable.ic_dialog_info)
        .setOngoing(true).setPriority(NotificationCompat.PRIORITY_LOW).build()

    private fun updateNotification(text: String) {
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFICATION_ID, buildNotification(text))
    }
}
