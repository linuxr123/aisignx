package com.aisignx.player.shell

import android.annotation.SuppressLint
import android.webkit.CookieManager
import android.webkit.WebView
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.aisignx.player.FileLog
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * window.AiSignXShellPort for the player page (WebViewCompat.addWebMessageListener).
 * The page posts JSON strings:
 *   {"t":"state","s":"PAIRED"}    -> flush the cookie store, so the credential survives a power cut
 *   {"t":"manifest","m":"<json>"} -> MediaCache.onManifest (prefetch)
 *
 * The listener is registered for the server origin only and ignores sub-frames,
 * so web items in iframes cannot reach it. Old WebViews without WEB_MESSAGE_LISTENER
 * get no port: the page's adapter treats that as a no-op and media is cached on demand.
 */
class ShellPort(
    private val onState: (String) -> Unit,
    private val onManifest: (String) -> Unit,
) {
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "aisignx-port").apply { isDaemon = true } }

    /** Register on [webView] before the first load. Returns false when unsupported. */
    @SuppressLint("RequiresFeature") // checked on the first line
    fun attach(webView: WebView, origin: String): Boolean {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            FileLog.i(TAG, "WEB_MESSAGE_LISTENER not supported; no prefetch on this WebView")
            return false
        }
        return try {
            WebViewCompat.addWebMessageListener(webView, JS_NAME, setOf(origin)) { _, message, _, isMainFrame, _ ->
                if (isMainFrame) message.data?.let { handle(it) }
            }
            true
        } catch (e: Exception) {
            FileLog.w(TAG, "addWebMessageListener failed: ${e.message}")
            false
        }
    }

    fun close() = worker.shutdownNow()

    private fun handle(raw: String) {
        if (raw.length > MAX_MESSAGE) return
        val msg = try { JSONObject(raw) } catch (_: Exception) { return }
        when (msg.optString("t")) {
            "state" -> {
                val s = msg.optString("s").take(32)
                if (s == "PAIRED") {
                    try { CookieManager.getInstance().flush() } catch (_: Exception) { }
                }
                onState(s)
            }
            "manifest" -> {
                val m = msg.opt("m")
                val json = when (m) {
                    is String -> m
                    is JSONObject -> m.toString()
                    else -> return
                }
                worker.execute { onManifest(json) }
            }
        }
    }

    companion object {
        private const val TAG = "ShellPort"
        const val JS_NAME = "AiSignXShellPort"
        private const val MAX_MESSAGE = 4 * 1024 * 1024
    }
}
