package com.aisignx.player

import android.net.Uri

/**
 * Normalize server base URLs for native clients (HTTPS on :443 behind Caddy).
 *
 * Migrates legacy http://host:5000 admin URLs to https://host so WebCache
 * intercept rules and on-disk media keys stay aligned with the player page.
 */
object ServerUrl {

    fun normalize(raw: String): String {
        var url = raw.trim().trimEnd('/')
        if (url.isEmpty()) return ""
        if (!url.contains("://")) {
            url = "https://$url"
        }
        return try {
            val uri = Uri.parse(url)
            val host = uri.host?.trim() ?: return url
            val port = uri.port
            when {
                port == 5000 || port == 80 || port == -1 || port == 443 ->
                    "https://$host"
                else ->
                    "https://$host:$port"
            }
        } catch (_: Throwable) {
            url
        }
    }

    /** True when [requestUrl] targets the same host as the configured [serverUrl]. */
    fun sameHost(requestUrl: String, serverUrl: String): Boolean {
        val base = normalize(serverUrl)
        if (base.isEmpty() || requestUrl.isBlank()) return false
        return try {
            val req = Uri.parse(requestUrl)
            val srv = Uri.parse(base)
            val reqHost = req.host?.lowercase()
            val srvHost = srv.host?.lowercase()
            !reqHost.isNullOrEmpty() && reqHost == srvHost &&
                (req.scheme == "http" || req.scheme == "https")
        } catch (_: Throwable) {
            requestUrl.startsWith(base)
        }
    }
}
