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
        const val BROADCAST_STATUS = "com.novelgen.app.SERVER_STATUS"
        var isServerReady = false
        var serverError: String? = null
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
        startForeground(NOTIFICATION_ID, buildNotification("初始化..."))

        Thread {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                val py = Python.getInstance()
                val module = py.getModule("server_runner")

                // Synchronous import test
                updateNotification("加载模块...")
                val result = module.callAttr("quick_test", apiKey)
                val ok = result.get("ok")?.toString() ?: "False"
                val step = result.get("step")?.toString() ?: "?"

                if (ok != "True") {
                    val err = result.get("error")?.toString() ?: "unknown"
                    serverError = err
                    broadcastStatus(false, "Step: $step\n$err")
                    updateNotification("失败: $step")
                    return@Thread
                }

                // Start uvicorn
                updateNotification("启动服务...")
                val ret = module.callAttr("start_uvicorn", "127.0.0.1", 8899)
                val started = ret.get("status")?.toString() ?: "error"

                if (started == "ok") {
                    isServerReady = true
                    broadcastStatus(true, null)
                    updateNotification("运行中 — 127.0.0.1:8899")
                } else {
                    serverError = started
                    broadcastStatus(false, started)
                    updateNotification("启动失败")
                }

            } catch (e: Exception) {
                serverError = e.message ?: "未知错误"
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
