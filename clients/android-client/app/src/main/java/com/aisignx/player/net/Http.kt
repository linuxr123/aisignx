package com.aisignx.player.net

import android.webkit.CookieManager
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

/**
 * OkHttp for the shell's own requests (setup test, /healthz probe, page snapshot and
 * media downloads). The WebView does its own networking for everything else.
 */
object Http {

    @Volatile private var cached: Pair<String, OkHttpClient>? = null

    /** One client per pin; connect 5 s, read 15 s; the pinning trust manager when pinned. */
    fun client(pin: String?): OkHttpClient {
        val key = pin.orEmpty()
        cached?.let { (k, c) -> if (k == key) return c }
        val c = build(pin)
        cached = key to c
        return c
    }

    fun build(pin: String?): OkHttpClient {
        val b = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .followRedirects(true)
            .followSslRedirects(false)
            .retryOnConnectionFailure(true)
        if (pin != null) {
            val tm = Tls.PinningTrustManager(pin)
            b.sslSocketFactory(Tls.sslContext(tm).socketFactory, tm)
            b.hostnameVerifier(Tls.hostnameVerifier(pin))
        }
        return b.build()
    }

    /** The WebView's cookie header for [url] (contains the display credential). Never log it. */
    fun cookieFor(url: String): String? =
        try { CookieManager.getInstance().getCookie(url) } catch (_: Throwable) { null }

    sealed class Health {
        object Ok : Health()
        object NotAiSignX : Health()
        data class Unreachable(val reason: String) : Health()
        /** TLS failed: the certificate is untrusted, or no longer matches the pin. */
        data class Untrusted(val reason: String) : Health()
    }

    /** GET <origin>/healthz and classify the answer. Blocking; call off the main thread. */
    fun health(client: OkHttpClient, origin: String): Health {
        val req = Request.Builder().url("$origin/healthz").header("Accept", "application/json").build()
        return try {
            client.newCall(req).execute().use { resp ->
                val body = if (resp.isSuccessful) resp.body?.string()?.take(4096) else null
                val app = try { body?.let { JSONObject(it).optString("app") } } catch (_: Exception) { null }
                if (app == "aisignx") Health.Ok else Health.NotAiSignX
            }
        } catch (e: SSLException) {
            Health.Untrusted(e.message ?: "TLS error")
        } catch (e: IOException) {
            Health.Unreachable(e.message ?: e.javaClass.simpleName)
        } catch (e: IllegalArgumentException) {
            Health.Unreachable(e.message ?: "invalid address")
        }
    }
}
