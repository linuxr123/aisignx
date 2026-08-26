/*
 * SPDX-License-Identifier: AGPL-3.0-or-later
 * Copyright (C) 2026 AISignX contributors
 */
package com.aisignx.player

import android.app.Application
import android.content.Context

class SignageApp : Application() {
    companion object {
        lateinit var instance: SignageApp
            private set

        // Prefs keys
        const val PREFS = "signage_prefs"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_TOKEN = "token"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_UPDATE_MODE = "update_mode"
        const val KEY_PLAYLIST_CACHE = "playlist_cache_json"
        const val UPDATE_MODE_AUTO = "auto"
        const val UPDATE_MODE_PROMPT = "prompt"
        const val UPDATE_MODE_MANUAL = "manual"
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        FileLog.init(this)
        migrateStoredServerUrl()
        FileLog.i("App", "AISignX Player started")
    }

    /** One-time migration: http://host:5000 → https://host for Caddy deployments. */
    private fun migrateStoredServerUrl() {
        val raw = prefs.getString(KEY_SERVER_URL, "") ?: ""
        if (raw.isEmpty()) return
        val normalized = ServerUrl.normalize(raw)
        if (normalized != raw) {
            prefs.edit().putString(KEY_SERVER_URL, normalized).apply()
            FileLog.i("App", "Migrated server URL $raw -> $normalized")
        }
    }

    val prefs get() = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
