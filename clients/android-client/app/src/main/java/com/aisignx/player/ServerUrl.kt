package com.aisignx.player

import java.net.URI
import java.util.Locale

/**
 * Server address rules shared with the Electron client's normalizeServerUrl():
 * trim, drop a trailing slash, add http:// when no scheme is given, allow http and
 * https only, reject userinfo, paths other than "/", queries and fragments.
 * The result is an origin such as "http://192.168.1.20:5000" (default ports dropped).
 * There is no port rewriting and no forced https.
 */
object ServerUrl {

    private val SCHEME = Regex("^[A-Za-z][A-Za-z0-9+.-]*://")

    /** The normalized origin, or null when the input is not an acceptable address. */
    fun normalize(raw: String?): String? {
        var s = raw?.trim() ?: return null
        if (s.isEmpty()) return null
        val hasScheme = SCHEME.containsMatchIn(s)
        while (s.endsWith("/")) s = s.dropLast(1)
        if (s.isEmpty()) return null
        if (!hasScheme) s = "http://$s"
        val uri = try { URI(s) } catch (_: Exception) { return null }
        val scheme = uri.scheme?.lowercase(Locale.ROOT) ?: return null
        if (scheme != "http" && scheme != "https") return null
        if (uri.rawUserInfo != null) return null
        val path = uri.rawPath ?: ""
        if (path.isNotEmpty() && path != "/") return null
        if (uri.rawQuery != null || uri.rawFragment != null) return null
        // A trailing "?" or "#" with nothing after it still means the user typed one.
        if (s.contains('?') || s.contains('#')) return null
        val host = uri.host?.lowercase(Locale.ROOT) ?: return null
        if (host.isEmpty()) return null
        val port = uri.port
        if (port == 0 || port > 65535) return null
        val defaultPort = if (scheme == "https") 443 else 80
        val portPart = if (port == -1 || port == defaultPort) "" else ":$port"
        return "$scheme://$host$portPart"
    }

    /** Host part of a normalized origin, without brackets for IPv6. */
    fun hostOf(origin: String): String =
        (try { URI(origin).host } catch (_: Exception) { null }).orEmpty()
            .removePrefix("[").removeSuffix("]").lowercase(Locale.ROOT)

    /** Whether [url] has the same scheme, host and port as [origin]. */
    fun sameOrigin(url: String, origin: String): Boolean {
        val a = try { URI(url) } catch (_: Exception) { return false }
        val b = try { URI(origin) } catch (_: Exception) { return false }
        val sa = a.scheme?.lowercase(Locale.ROOT) ?: return false
        if (sa != b.scheme?.lowercase(Locale.ROOT)) return false
        if (!a.host.equals(b.host, ignoreCase = true)) return false
        return effectivePort(sa, a.port) == effectivePort(sa, b.port)
    }

    private fun effectivePort(scheme: String, port: Int): Int =
        if (port != -1) port else if (scheme == "https") 443 else 80

    /**
     * True for addresses that are normally only reachable on a local network:
     * private and loopback IPv4/IPv6 literals, single-label names and local suffixes.
     * Used only to decide whether to show the plain-http warning (no DNS lookup).
     */
    fun isPrivateHost(rawHost: String): Boolean {
        val host = rawHost.trim().removePrefix("[").removeSuffix("]").lowercase(Locale.ROOT)
        if (host.isEmpty()) return false
        if (host == "localhost" || host.endsWith(".localhost")) return true
        val v4 = parseIpv4(host)
        if (v4 != null) {
            val (a, b) = v4
            return a == 10 || a == 127 || (a == 172 && b in 16..31) || (a == 192 && b == 168) ||
                (a == 169 && b == 254) || (a == 100 && b in 64..127)
        }
        if (host.contains(':')) {
            return host == "::1" || host.startsWith("fc") || host.startsWith("fd") ||
                host.startsWith("fe8") || host.startsWith("fe9") ||
                host.startsWith("fea") || host.startsWith("feb")
        }
        if (!host.contains('.')) return true
        val localSuffixes = listOf(".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet", ".corp")
        return localSuffixes.any { host.endsWith(it) }
    }

    private fun parseIpv4(host: String): Pair<Int, Int>? {
        val parts = host.split('.')
        if (parts.size != 4) return null
        val nums = parts.map { p -> p.toIntOrNull()?.takeIf { p.isNotEmpty() && it in 0..255 } ?: return null }
        return nums[0] to nums[1]
    }
}
