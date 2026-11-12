package com.aisignx.player

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
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

    /** Store a new server (and its pin, if the user trusted one). Commits synchronously. */
    @SuppressLint("ApplySharedPref") // the player starts right after and must see it
    fun saveServer(url: String, pin: String?) {
        prefs.edit().putString(KEY_SERVER, url).putString(KEY_PIN, pin).commit()
    }

    /** Forget the server and its pin. The client ID and home mode stay. */
    @SuppressLint("ApplySharedPref")
    fun forgetServer() {
        prefs.edit().remove(KEY_SERVER).remove(KEY_PIN).commit()
    }

    companion object {
        const val PREFS = "player_v3"
        private const val KEY_SERVER = "server_url"
        private const val KEY_PIN = "cert_pin"
        private const val KEY_CLIENT_ID = "client_id"
        private const val KEY_HOME = "home_mode"
        private const val KEY_V2_CLEANED = "v2_cleaned"

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
