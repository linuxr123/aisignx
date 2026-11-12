package com.aisignx.player.ui

import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog
import com.aisignx.player.ServerUrl
import com.aisignx.player.cache.Served
import com.aisignx.player.net.Http
import com.aisignx.player.net.Tls

/**
 * Loads the browser player and keeps it loaded.
 *
 * - shouldInterceptRequest: only GETs to the configured origin for the page/JS snapshot
 *   and the media cache. Everything else (POST, manifest, SSE, heartbeat, web items)
 *   returns null and the WebView handles it natively with its own cookie.
 * - Main-frame errors: 3 soft retries 2 s apart, then the offline page, with a /healthz
 *   probe every 10 s that reloads the player once the server answers.
 * - TLS: proceed() only for the configured host when the leaf matches the pin.
 */
class PlayerWebClient(
    private val services: AiSignXApp.Services,
    private val callbacks: Callbacks,
) : WebViewClient() {

    interface Callbacks {
        fun offlineReason(certChanged: Boolean): String
        fun onRenderProcessGone(view: WebView): Boolean
    }

    private val origin = services.origin
    private val host = ServerUrl.hostOf(origin)
    val playerUrl = origin + "/player/"

    private val main = Handler(Looper.getMainLooper())
    private var webView: WebView? = null
    private var failures = 0
    private var loadFailed = false
    private var playerLoaded = false
    @Volatile private var disposed = false

    fun start(view: WebView) {
        webView = view
        loadPlayer()
    }

    fun dispose() {
        disposed = true
        main.removeCallbacksAndMessages(null)
        webView = null
    }

    private fun loadPlayer() {
        val view = webView ?: return
        main.removeCallbacksAndMessages(null)
        playerLoaded = false
        view.loadUrl(playerUrl)
        main.postDelayed({ if (!playerLoaded) onMainFrameFailed("timeout") }, COLD_START_TIMEOUT_MS)
    }

    // ---- page lifecycle -------------------------------------------------------------

    override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
        if (url?.startsWith(playerUrl) == true) loadFailed = false
    }

    override fun onPageFinished(view: WebView, url: String?) {
        if (url?.startsWith(playerUrl) == true && !loadFailed && !playerLoaded) {
            playerLoaded = true
            failures = 0
            main.removeCallbacksAndMessages(null)
            FileLog.i(TAG, "player page loaded")
        }
    }

    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
        if (!request.isForMainFrame || !isPlayerUrl(request.url)) return
        onMainFrameFailed("error ${error.errorCode}")
    }

    override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
        if (request.isForMainFrame && isPlayerUrl(request.url) && response.statusCode in 502..504) {
            onMainFrameFailed("HTTP ${response.statusCode}")
        }
    }

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        if (!request.isForMainFrame) return false
        val url = request.url.toString()
        // The top frame only ever shows the player; iframes (web items) are not affected.
        val allowed = ServerUrl.sameOrigin(url, origin) || url.startsWith(OFFLINE_PAGE)
        if (!allowed) FileLog.w(TAG, "blocked top-level navigation to ${request.url.host}")
        return !allowed
    }

    override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
        FileLog.w(TAG, "renderer gone (crash=${detail.didCrash()})")
        dispose()
        return callbacks.onRenderProcessGone(view)
    }

    private fun onMainFrameFailed(reason: String) {
        if (disposed) return
        loadFailed = true
        main.removeCallbacksAndMessages(null)
        failures++
        FileLog.w(TAG, "player page failed ($reason), attempt $failures")
        if (failures <= SOFT_RETRIES) {
            main.postDelayed({ loadPlayer() }, SOFT_RETRY_MS)
        } else {
            showOffline(certChanged = false)
        }
    }

    private fun showOffline(certChanged: Boolean) {
        val view = webView ?: return
        main.removeCallbacksAndMessages(null)
        view.loadUrl(OFFLINE_PAGE + "#" + Uri.encode(callbacks.offlineReason(certChanged)))
        scheduleProbe()
    }

    /** Probe /healthz off the main thread every 10 s; reload the player once it answers. */
    private fun scheduleProbe() {
        main.postDelayed({
            if (disposed) return@postDelayed
            Thread({
                val result = Http.health(Http.client(services.pin), origin)
                main.post {
                    if (disposed) return@post
                    when (result) {
                        is Http.Health.Ok -> { failures = 0; loadPlayer() }
                        is Http.Health.Untrusted -> showOffline(certChanged = true)
                        else -> scheduleProbe()
                    }
                }
            }, "aisignx-probe").start()
        }, OFFLINE_RETRY_MS)
    }

    // ---- TLS ------------------------------------------------------------------------

    override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
        val pin = services.pin
        val errHost = try { Uri.parse(error.url).host?.lowercase() } catch (_: Exception) { null }
        val der = leafDer(error.certificate)
        val ok = pin != null && der != null && errHost == host && Tls.fingerprint(der) == pin
        if (ok) {
            handler.proceed()
            return
        }
        handler.cancel()
        FileLog.w(TAG, "TLS error ${error.primaryError} for $errHost; certificate not pinned or changed")
        if (errHost == host && isPlayerUrl(Uri.parse(error.url))) {
            main.post { if (!disposed) { loadFailed = true; showOffline(certChanged = true) } }
        }
    }

    private fun leafDer(cert: SslCertificate?): ByteArray? {
        cert ?: return null
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                cert.x509Certificate?.encoded
            } else {
                SslCertificate.saveState(cert).getByteArray("x509-certificate")
            }
        } catch (_: Exception) {
            null
        }
    }

    // ---- caches ---------------------------------------------------------------------

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        if (!request.method.equals("GET", ignoreCase = true)) return null
        val url = request.url
        if (!ServerUrl.sameOrigin(url.toString(), origin)) return null
        val path = url.encodedPath ?: return null
        return try {
            when {
                services.pageCache.handles(path) -> {
                    val q = url.encodedQuery
                    services.pageCache.fetch(if (q != null) "$path?$q" else path)?.toWeb()
                }
                services.mediaCache.handles(path) -> {
                    val range = request.requestHeaders.entries
                        .firstOrNull { it.key.equals("Range", ignoreCase = true) }?.value
                    services.mediaCache.serve(path, range)?.toWeb()
                }
                else -> null
            }
        } catch (e: Exception) {
            FileLog.e(TAG, "intercept failed for $path", e)
            null
        }
    }

    private fun Served.toWeb(): WebResourceResponse =
        WebResourceResponse(mime, charset, status, reason, headers, body)

    private fun isPlayerUrl(uri: Uri): Boolean =
        ServerUrl.sameOrigin(uri.toString(), origin) && (uri.path == "/player/" || uri.path == "/player")

    companion object {
        private const val TAG = "PlayerWeb"
        const val OFFLINE_PAGE = "file:///android_asset/offline.html"
        private const val SOFT_RETRIES = 3
        private const val SOFT_RETRY_MS = 2_000L
        private const val OFFLINE_RETRY_MS = 10_000L
        private const val COLD_START_TIMEOUT_MS = 25_000L
    }
}
