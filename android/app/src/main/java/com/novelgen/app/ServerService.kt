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

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(NotificationChannel(
                    "novelgen", "小说工坊", NotificationManager.IMPORTANCE_LOW))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val apiKey = intent?.getStringExtra("api_key") ?: ""
        val logDir = filesDir.absolutePath
        startForeground(1, NotificationCompat.Builder(this, "novelgen")
            .setContentTitle("小说工坊").setContentText("服务运行中")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true).setPriority(NotificationCompat.PRIORITY_LOW).build())

        // Clear old status file
        File(logDir, "novelgen_status.txt").delete()

        Thread {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                Python.getInstance().getModule("server_runner")
                    .callAttr("start_server", apiKey, "127.0.0.1", 8899, logDir)
            } catch (e: Exception) {
                // Write error to status file
                try {
                    File(logDir, "novelgen_status.txt")
                        .writeText("error_${e.message ?: "unknown"}")
                } catch (_: Exception) {}
            }
        }.start()

        return START_STICKY
    }
}
