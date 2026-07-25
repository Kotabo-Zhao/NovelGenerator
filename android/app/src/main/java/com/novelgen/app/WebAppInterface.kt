package com.novelgen.app

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Environment
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File

class WebAppInterface(private val context: Context) {

    @JavascriptInterface
    fun downloadFile(url: String, filename: String) {
        val request = DownloadManager.Request(Uri.parse(url))
            .setTitle(filename)
            .setDescription("下载中…")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)

        val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        dm.enqueue(request)
        Toast.makeText(context, "开始下载: $filename", Toast.LENGTH_SHORT).show()
    }

    @JavascriptInterface
    fun shareContent(title: String, text: String) {
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_SUBJECT, title)
            putExtra(Intent.EXTRA_TEXT, text)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(Intent.createChooser(intent, "分享"))
    }

    @JavascriptInterface
    fun toast(message: String) {
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        }
    }

    @JavascriptInterface
    fun savePreference(key: String, value: String) {
        context.getSharedPreferences("novelgen_web", Context.MODE_PRIVATE)
            .edit().putString(key, value).apply()
    }

    @JavascriptInterface
    fun loadPreference(key: String): String {
        return context.getSharedPreferences("novelgen_web", Context.MODE_PRIVATE)
            .getString(key, "") ?: ""
    }
}
