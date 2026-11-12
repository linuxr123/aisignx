package com.aisignx.player.shell

import android.os.Build
import android.webkit.JavascriptInterface
import com.aisignx.player.BuildConfig
import org.json.JSONObject

/**
 * Exposed to the player page as window.AiSignXShellInfo (addJavascriptInterface).
 * The page's net.shell() adapter reads info() to report "android-<version>", a
 * readable device name and the shell's client ID.
 *
 * Read-only and holds no secrets (no cookie, no token), so it does no harm if a
 * web item in an iframe calls it.
 */
class ShellInfo(
    private val version: String,
    private val device: String,
    private val clientId: String,
    private val adminUrl: String,
) {
    @JavascriptInterface
    fun info(): String = JSONObject()
        .put("name", NAME)
        .put("version", version)
        .put("device", device)
        .put("client_id", clientId)
        .put("admin_url", adminUrl)
        .toString()

    companion object {
        const val NAME = "android"
        const val JS_NAME = "AiSignXShellInfo"

        /** "Manufacturer Model / Android 14", trimmed to the server's 120 characters. */
        fun deviceName(manufacturer: String?, model: String?, release: String?): String {
            val maker = manufacturer?.trim().orEmpty()
            val mdl = model?.trim().orEmpty()
            val name = when {
                mdl.isEmpty() -> maker
                maker.isEmpty() || mdl.startsWith(maker, ignoreCase = true) -> mdl
                else -> "$maker $mdl"
            }.ifEmpty { "Android device" }
            return "$name / Android ${release?.trim().orEmpty().ifEmpty { "?" }}".take(120)
        }

        fun forThisDevice(clientId: String, adminUrl: String): ShellInfo = ShellInfo(
            version = BuildConfig.VERSION_NAME,
            device = deviceName(Build.MANUFACTURER, Build.MODEL, Build.VERSION.RELEASE),
            clientId = clientId,
            adminUrl = adminUrl,
        )
    }
}
