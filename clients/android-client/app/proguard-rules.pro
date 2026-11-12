# AiSignX Player release rules.

# The player page calls AiSignXShellInfo.info() through addJavascriptInterface.
# R8 must keep the class and every @JavascriptInterface method under its own name.
-keep class com.aisignx.player.shell.ShellInfo { *; }
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keepattributes JavascriptInterface

-dontwarn okhttp3.internal.platform.**
-dontwarn org.bouncycastle.**
-dontwarn org.conscrypt.**
-dontwarn org.openjsse.**
