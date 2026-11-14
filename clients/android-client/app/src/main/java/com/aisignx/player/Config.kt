package com.aisignx.player

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import com.aisignx.player.shell.DisplaySettings
import com.aisignx.player.shell.UnlockHash
import java.security.SecureRandom

/**
 * Device settings in the "player_v3" preferences.
 *
 * There is no token here: the display credential is the HttpOnly cookie that the
 * server sets in the WebView's CookieManager when the screen is paired.
 */
class Config(ctx: Context) {

    private val prefs: SharedPreferences =
        ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Normalized server origin, or null when setup has not been completed. */
    var serverUrl: String?
        get() = prefs.getString(KEY_SERVER, null)?.takeIf { it.isNotEmpty() }
        set(value) = prefs.edit().putString(KEY_SERVER, value).apply()

    /** SHA-256 hex (lowercase, no separators) of the pinned leaf certificate, or null. */
    var certPin: String?
        get() = prefs.getString(KEY_PIN, null)?.takeIf { PIN_RE.matches(it) }
        set(value) = prefs.edit().putString(KEY_PIN, value).apply()

    var homeMode: Boolean
        get() = prefs.getBoolean(KEY_HOME, false)
        set(value) = prefs.edit().putBoolean(KEY_HOME, value).apply()

    /** 32 hex characters, created once and kept when the server changes. */
    val clientId: String
        get() = ensureClientId()

    val isConfigured: Boolean get() = serverUrl != null

    /** Whether the one-time cleanup of v2 data has run. */
    var v2Cleaned: Boolean
        get() = prefs.getBoolean(KEY_V2_CLEANED, false)
        set(value) = prefs.edit().putBoolean(KEY_V2_CLEANED, value).apply()

    @SuppressLint("ApplySharedPref") // written before any page can read it
    @Synchronized
    fun ensureClientId(): String {
        val existing = prefs.getString(KEY_CLIENT_ID, null)
        if (existing != null && CLIENT_ID_RE.matches(existing)) return existing
        val id = newClientId()
        prefs.edit().putString(KEY_CLIENT_ID, id).commit()
        return id
    }

    /** Hash of this display's unlock PIN from the last manifest, or null when none is set. */
    val unlockHash: UnlockHash?
        get() = UnlockHash.fromPref(prefs.getString(KEY_UNLOCK_HASH, null))

    /** Configured orientation in degrees, or -1 before the first manifest with settings. */
    val displayOrientation: Int
        get() = prefs.getInt(KEY_ORIENTATION, -1).takeIf { it in DisplaySettings.ORIENTATIONS } ?: -1

    /** True until settings arrive, which keeps the old behaviour with older servers. */
    val allowInput: Boolean
        get() = prefs.getBoolean(KEY_ALLOW_INPUT, true)

    /** Lock player (lock task) from the last manifest; off until settings arrive. */
    val kioskLock: Boolean
        get() = prefs.getBoolean(KEY_KIOSK_LOCK, false)

    /** Wrong PINs in a row and the time (wall clock ms) until which the prompt is locked. */
    val unlockFailures: Int get() = prefs.getInt(KEY_UNLOCK_FAILURES, 0)
    val unlockLockedUntil: Long get() = prefs.getLong(KEY_UNLOCK_LOCKED_UNTIL, 0L)

    @SuppressLint("ApplySharedPref") // kept across a power cut right after the change
    fun saveDisplaySettings(s: DisplaySettings) {
        val e = prefs.edit()
            .putInt(KEY_ORIENTATION, s.orientation)
            .putBoolean(KEY_ALLOW_INPUT, s.allowInput)
            .putBoolean(KEY_KIOSK_LOCK, s.kioskLock)
        val old = prefs.getString(KEY_UNLOCK_HASH, null)
        val new = s.unlock?.toPref()
        if (new == null) e.remove(KEY_UNLOCK_HASH) else e.putString(KEY_UNLOCK_HASH, new)
        if (old != new) e.remove(KEY_UNLOCK_FAILURES).remove(KEY_UNLOCK_LOCKED_UNTIL)
        e.commit()
    }

    @SuppressLint("ApplySharedPref") // must survive a restart, or the backoff is bypassed
    fun saveUnlockState(failures: Int, lockedUntil: Long) {
        prefs.edit().putInt(KEY_UNLOCK_FAILURES, failures).putLong(KEY_UNLOCK_LOCKED_UNTIL, lockedUntil).commit()
    }

    /** Store a new server (and its pin, if the user trusted one). Commits synchronously. */
    @SuppressLint("ApplySharedPref") // the player starts right after and must see it
    fun saveServer(url: String, pin: String?) {
        val e = prefs.edit()
        // Another server's settings (and its unlock PIN) do not apply to this one.
        if (prefs.getString(KEY_SERVER, null) != url) removeDisplaySettings(e)
        e.putString(KEY_SERVER, url).putString(KEY_PIN, pin).commit()
    }

    /** Forget the server, its pin and the display settings. The client ID and home mode stay. */
    @SuppressLint("ApplySharedPref")
    fun forgetServer() {
        removeDisplaySettings(prefs.edit().remove(KEY_SERVER).remove(KEY_PIN)).commit()
    }

    private fun removeDisplaySettings(e: SharedPreferences.Editor): SharedPreferences.Editor =
        e.remove(KEY_UNLOCK_HASH).remove(KEY_UNLOCK_FAILURES).remove(KEY_UNLOCK_LOCKED_UNTIL)
            .remove(KEY_ORIENTATION).remove(KEY_ALLOW_INPUT).remove(KEY_KIOSK_LOCK)

    companion object {
        const val PREFS = "player_v3"
        private const val KEY_SERVER = "server_url"
        private const val KEY_PIN = "cert_pin"
        private const val KEY_CLIENT_ID = "client_id"
        private const val KEY_HOME = "home_mode"
        private const val KEY_V2_CLEANED = "v2_cleaned"
        private const val KEY_UNLOCK_HASH = "unlock_hash"
        private const val KEY_UNLOCK_FAILURES = "unlock_failures"
        private const val KEY_UNLOCK_LOCKED_UNTIL = "unlock_locked_until"
        private const val KEY_ORIENTATION = "display_orientation"
        private const val KEY_ALLOW_INPUT = "allow_input"
        private const val KEY_KIOSK_LOCK = "kiosk_lock"

        val CLIENT_ID_RE = Regex("^[0-9a-f]{32}$")
        val PIN_RE = Regex("^[0-9a-f]{64}$")

        /** 16 random bytes as 32 lowercase hex characters. */
        fun newClientId(random: SecureRandom = SecureRandom()): String {
            val bytes = ByteArray(16)
            random.nextBytes(bytes)
            return bytes.joinToString("") { "%02x".format(it.toInt() and 0xff) }
        }
    }
}
