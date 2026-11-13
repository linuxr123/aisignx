package com.aisignx.player.shell

import android.content.pm.ActivityInfo
import org.json.JSONObject

/**
 * The parts of manifest.settings that the Android shell applies natively:
 * screen orientation (non-TV only), the input lock and the unlock PIN hash.
 * Volume, the offline banner and CSS rotation are handled by the page.
 */
data class DisplaySettings(val orientation: Int, val allowInput: Boolean, val unlock: UnlockHash?) {

    companion object {
        val ORIENTATIONS = setOf(0, 90, 180, 270)

        /**
         * Parse a manifest JSON string. Null when it has no "settings" object (an older
         * server), so the stored settings are left alone. Bad values fall back to
         * defaults; a bad unlock object counts as "no PIN", like the Electron sanitizer.
         */
        fun parse(manifestJson: String): DisplaySettings? {
            val root = try { JSONObject(manifestJson) } catch (_: Exception) { return null }
            val s = root.optJSONObject("settings") ?: return null
            val deg = s.opt("orientation")
            val orientation = if (deg is Int && deg in ORIENTATIONS) deg else 0
            val allow = s.opt("allow_input") == true
            return DisplaySettings(orientation, allow, parseUnlock(s.optJSONObject("unlock")))
        }

        fun parseUnlock(u: JSONObject?): UnlockHash? {
            if (u == null || u.opt("alg") != UnlockHash.ALG) return null
            val iter = u.opt("iter") as? Int ?: return null
            val salt = u.opt("salt") as? String ?: return null
            val hash = u.opt("hash") as? String ?: return null
            return UnlockHash.of(iter, salt, hash)
        }

        /** Degrees the content is turned to the activity's requested orientation. */
        fun orientationFlag(deg: Int): Int = when (deg) {
            90 -> ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            180 -> ActivityInfo.SCREEN_ORIENTATION_REVERSE_LANDSCAPE
            270 -> ActivityInfo.SCREEN_ORIENTATION_REVERSE_PORTRAIT
            else -> ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        }
    }
}
