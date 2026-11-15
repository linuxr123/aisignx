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
 *   {"t":"controls"}              -> keep this message's reply proxy; control actions from keys
 *                                    and remote buttons go back on it as
 *                                    {"t":"control","a":"next","r":false} (see sendControl)
 *   {"t":"alive","a":{...}}       -> the page runs (about every 10 s); feeds the hang watchdog
 *   {"t":"command","c":{...}}     -> an SSE command the page forwards: unlock or lock (onCommand)
 *
 * The listener is registered for the server origin only and ignores sub-frames,
 * so web items in iframes cannot reach it. Old WebViews without WEB_MESSAGE_LISTENER
 * get no port: the page's adapter treats that as a no-op and media is cached on demand.
 */
class ShellPort(
    private val onState: (String) -> Unit,
    private val onManifest: (String) -> Unit,
    private val onCapture: (id: Int, reply: (String?) -> Unit) -> Unit = { _, reply -> reply(null) },
    private val onCommand: (Command) -> Unit = {},
    private val onAlive: () -> Unit = {},
) {
    private val main = Handler(Looper.getMainLooper())

    /** Reply proxy of the page's last {"t":"controls"}; main thread only. */
    private var controlProxy: JavaScriptReplyProxy? = null
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
            "controls" -> controlProxy = replyProxy
            "alive" -> onAlive() // the page is running; only the arrival counts, the payload is ignored
            "command" -> {
                val c = msg.optJSONObject("c") ?: return
                parseCommand(c)?.let(onCommand)
            }
        }
    }

    /** Main thread. Sends a control action to the page; a no-op until it registered. */
    @SuppressLint("RequiresFeature") // a reply proxy only exists when attach() succeeded
    fun sendControl(action: String, repeat: Boolean) {
        val proxy = controlProxy ?: return
        try {
            proxy.postMessage(controlMessage(action, repeat))
        } catch (e: Exception) { // page gone or navigated away
            FileLog.w(TAG, "control send failed: ${e.message}")
            controlProxy = null
        }
    }

    private fun reply(proxy: JavaScriptReplyProxy, json: String) {
        try {
            proxy.postMessage(json)
        } catch (e: Exception) { // page gone or navigated away
            FileLog.w(TAG, "capture reply failed: ${e.message}")
        }
    }

    /** A validated SSE command. [minutes] is 0 for lock. */
    data class Command(val action: String, val minutes: Int, val requestId: String)

    companion object {
        private const val TAG = "ShellPort"
        private const val MAX_ID = 1_000_000_000
        const val UNLOCK = "unlock"
        const val LOCK = "lock"
        const val MIN_UNLOCK_MINUTES = 1
        const val MAX_UNLOCK_MINUTES = 240
        private val REQUEST_ID_RE = Regex("^[0-9a-f]{16}$")

        /** {"t":"control","a":action,"r":repeat} */
        fun controlMessage(action: String, repeat: Boolean): String =
            JSONObject().put("t", "control").put("a", action).put("r", repeat).toString()

        /**
         * Only unlock (integer minutes, clamped to 1..240) and lock are accepted, each
         * with a 16-hex request_id. Anything else is null and ignored.
         */
        fun parseCommand(c: JSONObject): Command? {
            val rid = c.opt("request_id") as? String ?: return null
            if (!REQUEST_ID_RE.matches(rid)) return null
            return when (c.opt("action")) {
                UNLOCK -> {
                    val m = c.opt("minutes") as? Int ?: return null
                    Command(UNLOCK, m.coerceIn(MIN_UNLOCK_MINUTES, MAX_UNLOCK_MINUTES), rid)
                }
                LOCK -> Command(LOCK, 0, rid)
                else -> null
            }
        }

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
