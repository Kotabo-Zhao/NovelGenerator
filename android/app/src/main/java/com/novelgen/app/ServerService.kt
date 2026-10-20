package com.novelgen.app

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class ServerService : Service() {
    private val started = AtomicBoolean(false)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(NotificationChannel(
                    "novelgen", "墨局", NotificationManager.IMPORTANCE_LOW))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val updateOnly = intent?.getBooleanExtra("update_only", false) ?: false
        val internalBuild = intent?.getBooleanExtra("internal_build", BuildConfig.INTERNAL_BUILD)
            ?: BuildConfig.INTERNAL_BUILD
        if (!updateOnly && !started.compareAndSet(false, true)) return START_STICKY
        val apiKey = intent?.getStringExtra("api_key")
            ?: getSharedPreferences("novelgen", MODE_PRIVATE).getString("api_key", "") ?: ""
        val logDir = filesDir.absolutePath
        val statusFile = File(logDir, "novelgen_status.txt")
        statusFile.delete()

        startForeground(1, NotificationCompat.Builder(this, "novelgen")
            .setContentTitle("墨局").setContentText("本地服务运行中")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true).setPriority(NotificationCompat.PRIORITY_LOW).build())

        if (updateOnly) {
            Thread {
                try {
                    if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                    Python.getInstance().getModule("server_runner")
                        .callAttr("update_api_key", apiKey)
                } catch (e: Exception) {
                    try { statusFile.writeText("error_key_update_${e.message ?: "unknown"}") } catch (_: Exception) {}
                }
            }.start()
            return START_STICKY
        }

        Thread {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                val result = Python.getInstance().getModule("server_runner")
                    .callAttr("start_server", apiKey, "127.0.0.1", 8899, logDir, internalBuild)

                val status = result.get("status")?.toString() ?: "unknown"
                if (status == "error") {
                    started.set(false)
                }
                // If status is "done", uvicorn thread is starting — 
                // the Python code writes "server_ready" when it's actually serving
            } catch (e: Exception) {
                started.set(false)
                try {
                    statusFile.writeText("error_${e.message ?: "unknown"}")
                } catch (_: Exception) {}
            }
        }.start()

        return START_STICKY
    }
}
