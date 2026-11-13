package com.aisignx.player.shell

import java.security.MessageDigest
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/**
 * The display's unlock PIN as sent by the server in manifest.settings.unlock:
 * PBKDF2-HMAC-SHA256 with [iter] iterations, a 16-byte salt and a 32-byte hash.
 * Only the hash is ever stored; the PIN itself never leaves the dialog.
 */
data class UnlockHash(val iter: Int, val saltHex: String, val hashHex: String) {

    /** "iter$salt$hash", the format kept in the preferences. */
    fun toPref(): String = "$iter$$saltHex$$hashHex"

    companion object {
        const val ALG = "pbkdf2-sha256"
        const val MIN_ITER = 10_000
        const val MAX_ITER = 1_000_000
        private val SALT_RE = Regex("^[0-9a-f]{32}$")
        private val HASH_RE = Regex("^[0-9a-f]{64}$")

        /** Validated hash, or null when any part is out of range. */
        fun of(iter: Int, saltHex: String, hashHex: String): UnlockHash? =
            if (iter in MIN_ITER..MAX_ITER && SALT_RE.matches(saltHex) && HASH_RE.matches(hashHex)) {
                UnlockHash(iter, saltHex, hashHex)
            } else {
                null
            }

        fun fromPref(s: String?): UnlockHash? {
            val parts = s?.split('$') ?: return null
            if (parts.size != 3) return null
            val iter = parts[0].toIntOrNull() ?: return null
            return of(iter, parts[1], parts[2])
        }
    }
}

object Unlock {
    private val PIN_RE = Regex("^[0-9]{1,12}$")
    const val FREE_FAILURES = 5
    const val FIRST_WAIT_MS = 60_000L
    const val MAX_WAIT_MS = 15 * 60_000L

    /** Constant-time check of [pin] against [h]. Slow on purpose: call it off the UI thread. */
    fun verify(pin: String, h: UnlockHash): Boolean {
        if (!PIN_RE.matches(pin)) return false
        val actual = derive(pin, hex(h.saltHex), h.iter)
        return MessageDigest.isEqual(actual, hex(h.hashHex))
    }

    fun derive(pin: String, salt: ByteArray, iter: Int): ByteArray {
        val spec = PBEKeySpec(pin.toCharArray(), salt, iter, 256)
        try {
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
        }
    }

    /**
     * Lockout after [failures] wrong PINs in a row: none for the first 4, then 60 s,
     * doubling with every further failure up to 15 minutes (same policy as Electron).
     */
    fun waitMs(failures: Int): Long {
        if (failures < FREE_FAILURES) return 0L
        val steps = (failures - FREE_FAILURES).coerceAtMost(10)
        return (FIRST_WAIT_MS shl steps).coerceAtMost(MAX_WAIT_MS)
    }

    fun hex(s: String): ByteArray = ByteArray(s.length / 2) { i ->
        s.substring(2 * i, 2 * i + 2).toInt(16).toByte()
    }
}
