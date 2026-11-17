package com.aisignx.player.provision

import com.aisignx.player.ServerUrl
import org.json.JSONObject

/**
 * The decisions of QR provisioning (a factory-reset tablet set up as a fully managed device), as plain
 * Kotlin without Android calls (JVM unit tests). See docs/android-kiosk.md.
 */
object ProvisionRules {

    /** DevicePolicyManager.PROVISIONING_MODE_FULLY_MANAGED_DEVICE (Android 12+). */
    const val MODE_FULLY_MANAGED = 1

    /** The mode to answer GET_PROVISIONING_MODE with, or null (cancel) when fully managed is not offered. */
    fun chooseMode(allowed: List<Int>?): Int? =
        if (allowed.isNullOrEmpty() || MODE_FULLY_MANAGED in allowed) MODE_FULLY_MANAGED else null

    /** [commandKey]: the server's public command key (32 raw bytes) to pin, or null when absent or unusable. */
    class Extras(val serverUrl: String, val secret: String, val commandKey: ByteArray? = null) {
        override fun toString() = "Extras($serverUrl, secret=[hidden], commandKey=${commandKey != null})" // never print the secret
    }

    private val SECRET_RE = Regex("^[\\x21-\\x7e]{1,128}$")

    /**
     * The admin extras bundle {server_url, pairing_secret, command_key}; null when the address or the secret is
     * missing or unusable. A missing or malformed command key does not stop provisioning (the device then pins
     * the key on first contact instead); it is never half-accepted. The address must be a plain origin (no path),
     * exactly as the server's clean_base_url requires, so both sides agree.
     */
    fun parseExtras(serverUrl: String?, secret: String?, commandKey: String? = null): Extras? {
        val url = ServerUrl.normalize(serverUrl) ?: return null
        if (secret == null || !SECRET_RE.matches(secret)) return null
        return Extras(url, secret, parseCommandKey(commandKey))
    }

    /** Base64 of exactly 32 raw bytes (a public Ed25519 key), else null. */
    fun parseCommandKey(text: String?): ByteArray? {
        if (text == null || text.length > 64) return null
        val raw = try { java.util.Base64.getDecoder().decode(text) } catch (_: IllegalArgumentException) { return null }
        return raw.takeIf { it.size == com.aisignx.player.shell.Ed25519.KEY_BYTES }
    }

    enum class Claim { PAIRED, RETRY, GIVE_UP }

    /**
     * The answer of POST /api/player/v1/pair/status. [status] is the JSON "status" member when the body
     * could be read. 200 "paired" is done; "pending" and server trouble are tried again; an expired or
     * rejected secret (404, other 4xx) can never work, so the player falls back to its normal pairing code.
     */
    fun classify(httpCode: Int, status: String?): Claim = when {
        httpCode == 200 && status == "paired" -> Claim.PAIRED
        httpCode == 200 -> Claim.RETRY
        httpCode == 429 || httpCode >= 500 || httpCode == 408 -> Claim.RETRY
        else -> Claim.GIVE_UP
    }

    /** Stop retrying after this long and fall back to normal setup (a wrong address would retry for hours). */
    const val GIVE_UP_AFTER_MS = 10 * 60_000L

    fun timedOut(startedMs: Long, nowMs: Long): Boolean = nowMs - startedMs >= GIVE_UP_AFTER_MS

    /**
     * What a failed connection means. Only a genuine certificate problem gives up (a person must trust the
     * certificate in setup). Everything else is tried again: dropped connections, interrupted handshakes, and
     * a certificate that is "not yet valid" because a freshly reset tablet's clock is still wrong.
     */
    fun classifyFailure(e: Throwable): Claim {
        var t: Throwable? = e
        var certificate = false
        var depth = 0
        while (t != null && depth++ < 8) {
            if (t is java.security.cert.CertificateNotYetValidException) return Claim.RETRY
            if (t is javax.net.ssl.SSLPeerUnverifiedException || t is java.security.cert.CertificateException ||
                t is java.security.cert.CertPathValidatorException
            ) certificate = true
            t = t.cause
        }
        return if (certificate) Claim.GIVE_UP else Claim.RETRY
    }

    /** The hidden exit: [TAPS] quick taps, each within [TAP_GAP_MS] of the one before. */
    const val TAPS = 7
    const val TAP_GAP_MS = 2_000L

    class TapCounter {
        private var count = 0
        private var last = 0L
        /** Returns true when this tap completes the gesture. */
        fun tap(nowMs: Long): Boolean {
            count = if (count > 0 && nowMs - last <= TAP_GAP_MS && nowMs >= last) count + 1 else 1
            last = nowMs
            if (count >= TAPS) { count = 0; return true }
            return false
        }
    }

    fun statusOf(body: String): String? = try { JSONObject(body).opt("status") as? String } catch (_: Exception) { null }

    /** Waiting time before attempt number [attempt] (1 = first retry): 3, 5, 10, 20, then 30 seconds. */
    fun delayMs(attempt: Int): Long = when {
        attempt <= 1 -> 3_000L
        attempt == 2 -> 5_000L
        attempt == 3 -> 10_000L
        attempt == 4 -> 20_000L
        else -> 30_000L
    }

    /** Only the display credential cookie is taken from the answer; anything else is ignored. */
    fun displayCookies(setCookieHeaders: List<String>): List<String> =
        setCookieHeaders.filter { it.startsWith("aisignx_display=") }
}
