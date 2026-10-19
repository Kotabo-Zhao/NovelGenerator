package com.novelgen.app

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.ContentValues
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import android.webkit.JavascriptInterface
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File

class WebAppInterface(private val context: Context) {

    /** Save WebView blob exports in Downloads, including UTF-8 cards and chats. */
    @JavascriptInterface
    fun saveFile(filename: String, mimeType: String, base64: String) {
        Thread {
            try {
                val safeName = filename.replace(Regex("[\\\\/:*?\"<>|\\p{Cntrl}]"), "_")
                    .trim().take(180).ifBlank { "墨局导出.txt" }
                val bytes = Base64.decode(base64, Base64.DEFAULT)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    val resolver = context.contentResolver
                    val values = ContentValues().apply {
                        put(MediaStore.Downloads.DISPLAY_NAME, safeName)
                        put(MediaStore.Downloads.MIME_TYPE, mimeType)
                        put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/墨局")
                        put(MediaStore.Downloads.IS_PENDING, 1)
                    }
                    val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                        ?: error("无法创建文件")
                    try {
                        val output = resolver.openOutputStream(uri) ?: error("无法打开文件")
                        output.use { it.write(bytes) }
                        values.clear()
                        values.put(MediaStore.Downloads.IS_PENDING, 0)
                        resolver.update(uri, values, null, null)
                    } catch (error: Exception) {
                        resolver.delete(uri, null, null)
                        throw error
                    }
                    toast("已保存到 下载/墨局/$safeName")
                } else {
                    // Android 8/9: use the system document UI without storage permission.
                    val folder = File(context.cacheDir, "downloads").apply { mkdirs() }
                    val file = File(folder, safeName)
                    file.writeBytes(bytes)
                    val uri = FileProvider.getUriForFile(context, context.packageName + ".fileprovider", file)
                    val share = Intent(Intent.ACTION_SEND).apply {
                        type = mimeType
                        putExtra(Intent.EXTRA_STREAM, uri)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                    context.startActivity(Intent.createChooser(share, "保存或分享 $safeName")
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            } catch (_: Exception) {
                toast("文件保存失败，请检查可用空间后重试")
            }
        }.start()
    }

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
