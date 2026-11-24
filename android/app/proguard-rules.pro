# Keep JavaScript interface
-keepclassmembers class com.novelgen.app.WebAppInterface {
    @android.webkit.JavascriptInterface *;
}

# Keep WebView
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
