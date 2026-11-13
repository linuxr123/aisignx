package com.aisignx.player.shell

import android.annotation.SuppressLint
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.webkit.WebView
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.aisignx.player.FileLog
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * window.AiSignXShellPort for the player page (WebViewCompat.addWebMessageListener).
 * The page posts JSON strings:
 *   {"t":"state","s":"PAIRED"}    -> flush the cookie store, so the credential survives a power cut
 *   {"t":"manifest","m":"<json>"} -> onManifest on a worker thread (settings, prefetch)
 *   {"t":"capture","id":n}        -> onCapture; the shell answers on the same port with
 *                                    {"t":"capture","id":n,"ok":true,"jpeg":"<base64>"} or "ok":false
 *
 * The listener is registered for the server origin only and ignores sub-frames,
 * so web items in iframes cannot reach it. Old WebViews without WEB_MESSAGE_LISTENER
 * get no port: the page's adapter treats that as a no-op and media is cached on demand.
 */
class ShellPort(
    private val onState: (String) -> Unit,
    private val onManifest: (String) -> Unit,
    private val onCapture: (id: Int, reply: (String?) -> Unit) -> Unit = { _, reply -> reply(null) },
) {
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "aisignx-port").apply { isDaemon = true } }

    /** Register on [webView] before the first load. Returns false when unsupported. */
    @SuppressLint("RequiresFeature") // checked on the first line
    fun attach(webView: WebView, origin: String): Boolean {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            FileLog.i(TAG, "WEB_MESSAGE_LISTENER not supported; no prefetch on this WebView")
            return false
        }
        return try {
            WebViewCompat.addWebMessageListener(webView, JS_NAME, setOf(origin)) { _, message, _, isMainFrame, replyProxy ->
                if (isMainFrame) message.data?.let { handle(it, replyProxy) }
            }
            true
        } catch (e: Exception) {
            FileLog.w(TAG, "addWebMessageListener failed: ${e.message}")
            false
        }
    }

    fun close() = worker.shutdownNow()

    /** Runs on the main thread (WebMessageListener contract). */
    private fun handle(raw: String, replyProxy: JavaScriptReplyProxy) {
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
            "capture" -> {
                val id = msg.opt("id") as? Int ?: return
                if (id !in 1..MAX_ID) return
                onCapture(id) { jpeg -> main.post { reply(replyProxy, captureReply(id, jpeg)) } }
            }
        }
    }

    private fun reply(proxy: JavaScriptReplyProxy, json: String) {
        try {
            proxy.postMessage(json)
        } catch (e: Exception) { // page gone or navigated away
            FileLog.w(TAG, "capture reply failed: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "ShellPort"
        private const val MAX_ID = 1_000_000_000

        /** The reply the page waits for; [jpeg] is Base64 or null on failure. */
        fun captureReply(id: Int, jpeg: String?): String {
            val o = JSONObject().put("t", "capture").put("id", id).put("ok", jpeg != null)
            if (jpeg != null) o.put("jpeg", jpeg)
            return o.toString()
        }
        const val JS_NAME = "AiSignXShellPort"
        private const val MAX_MESSAGE = 4 * 1024 * 1024
    }
}
