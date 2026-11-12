package com.aisignx.player.net

import okhttp3.internal.tls.OkHostnameVerifier
import java.net.InetSocketAddress
import java.net.URI
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * TLS trust for the configured server.
 *
 * Order of trust: the platform trust store (system and user CAs) first; otherwise only
 * a leaf certificate whose SHA-256 equals the pin the user accepted in setup (TOFU).
 * There is no trust-all manager and nothing is accepted without a pin match.
 */
object Tls {

    /** SHA-256 of a DER certificate as lowercase hex without separators. */
    fun fingerprint(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    /** "AB:CD:..." for display (matches openssl x509 -fingerprint -sha256). */
    fun colonHex(hex: String): String = hex.uppercase(Locale.ROOT).chunked(2).joinToString(":")

    fun platformTrustManager(): X509TrustManager {
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(null as KeyStore?)
        return tmf.trustManagers.filterIsInstance<X509TrustManager>().first()
    }

    /** Platform trust first; on failure accept only a leaf whose SHA-256 equals [pin]. */
    class PinningTrustManager(
        private val pin: String?,
        private val base: X509TrustManager = platformTrustManager(),
    ) : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            base.checkClientTrusted(chain, authType)

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            try {
                base.checkServerTrusted(chain, authType)
            } catch (e: CertificateException) {
                if (pin != null && chain.isNotEmpty() && fingerprint(chain[0].encoded) == pin) return
                throw e
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = base.acceptedIssuers
    }

    /** The default hostname check, or a pin match on the leaf the server presented. */
    fun hostnameVerifier(pin: String?): HostnameVerifier = HostnameVerifier { host, session ->
        if (OkHostnameVerifier.verify(host, session)) return@HostnameVerifier true
        if (pin == null) return@HostnameVerifier false
        val leaf = try { session.peerCertificates.firstOrNull() } catch (_: Exception) { null }
        leaf != null && fingerprint(leaf.encoded) == pin
    }

    fun sslContext(tm: TrustManager): SSLContext =
        SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }

    /** What setup shows on the trust card. */
    data class CertInfo(
        val sha256: String,
        val subject: String,
        val issuer: String,
        val validTo: String,
        val host: String,
        val selfSigned: Boolean,
    )

    private class CapturedException : CertificateException("captured")

    /**
     * Read the certificate an https server presents. The trust manager records the
     * chain and then throws, so the handshake never completes and no HTTP bytes are
     * sent. Returns null when nothing could be read (unreachable, not TLS).
     */
    fun capture(url: String, timeoutMs: Int = 5000): CertInfo? {
        val uri = try { URI(url) } catch (_: Exception) { return null }
        if (!uri.scheme.equals("https", ignoreCase = true)) return null
        val host = uri.host?.removePrefix("[")?.removeSuffix("]") ?: return null
        val port = if (uri.port == -1) 443 else uri.port
        var chain: Array<X509Certificate>? = null
        val recorder = object : X509TrustManager {
            override fun checkClientTrusted(c: Array<X509Certificate>, a: String) = throw CapturedException()
            override fun checkServerTrusted(c: Array<X509Certificate>, a: String) {
                chain = c
                throw CapturedException()
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val factory = sslContext(recorder).socketFactory
        try {
            java.net.Socket().use { raw ->
                raw.connect(InetSocketAddress(host, port), timeoutMs)
                raw.soTimeout = timeoutMs
                (factory.createSocket(raw, host, port, true) as SSLSocket).use { it.startHandshake() }
            }
        } catch (_: Exception) {
            // Expected: the recorder aborts the handshake.
        }
        val leaf = chain?.firstOrNull() ?: return null
        return CertInfo(
            sha256 = fingerprint(leaf.encoded),
            subject = leaf.subjectX500Principal.name,
            issuer = leaf.issuerX500Principal.name,
            validTo = isoDate(leaf.notAfter.time),
            host = host,
            selfSigned = leaf.subjectX500Principal == leaf.issuerX500Principal,
        )
    }

    private fun isoDate(ms: Long): String =
        SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
            .format(java.util.Date(ms))
}
