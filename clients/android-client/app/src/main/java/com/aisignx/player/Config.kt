package com.aisignx.player

import android.content.Context
import java.util.UUID

object Config {

    private val prefs get() = SignageApp.instance.prefs

    var serverUrl: String
        get() = prefs.getString(SignageApp.KEY_SERVER_URL, "") ?: ""
        set(v) = prefs.edit().putString(SignageApp.KEY_SERVER_URL, v.trimEnd('/')).apply()

    var token: String
        get() = prefs.getString(SignageApp.KEY_TOKEN, "") ?: ""
        set(v) = prefs.edit().putString(SignageApp.KEY_TOKEN, v).apply()

    /** Stable unique device ID â€” generated once, never changes. */
    val deviceId: String
        get() {
            var id = prefs.getString(SignageApp.KEY_DEVICE_ID, null)
            if (id.isNullOrEmpty()) {
                id = UUID.randomUUID().toString()
                prefs.edit().putString(SignageApp.KEY_DEVICE_ID, id).apply()
            }
            return id
        }

    val isConfigured get() = serverUrl.isNotEmpty() && token.isNotEmpty()
    val hasServer    get() = serverUrl.isNotEmpty()

    var updateMode: String
        get() = prefs.getString(SignageApp.KEY_UPDATE_MODE, SignageApp.UPDATE_MODE_PROMPT)
            ?: SignageApp.UPDATE_MODE_PROMPT
        set(v) = prefs.edit().putString(SignageApp.KEY_UPDATE_MODE, v).apply()

    fun clear() {
        prefs.edit().remove(SignageApp.KEY_TOKEN).remove(SignageApp.KEY_SERVER_URL).apply()
    }
}
