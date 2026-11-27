package com.aisignx.player.shell

import android.os.Build
import android.webkit.JavascriptInterface
import com.aisignx.player.BuildConfig
import org.json.JSONArray
import org.json.JSONObject

/**
 * Exposed to the player page as window.AiSignXShellInfo (addJavascriptInterface).
 * The page's net.shell() adapter reads info() to report "android-<version>", a
 * readable device name, the shell's client ID and its capabilities, and stats() for
 * the heartbeat diagnostics.
 *
 * Read-only and holds no secrets (no cookie, no token, no unlock hash), so it does
 * no harm if a web item in an iframe calls it.
 */
class ShellInfo(
    private val version: String,
    private val device: String,
    private val clientId: String,
    private val adminUrl: String,
    private val caps: List<String> = emptyList(),
    private val stats: () -> String = { "{}" },
) {
    @JavascriptInterface
    fun info(): String = JSONObject()
        .put("name", NAME)
        .put("version", version)
        .put("device", device)
        .put("client_id", clientId)
        .put("admin_url", adminUrl)
        .put("caps", JSONArray(caps))
        .toString()

    /** {uptime_s, cache: {files, bytes}, storage_free_bytes, lock} as JSON; "{}" on error. */
    @JavascriptInterface
    fun stats(): String = try { stats.invoke() } catch (_: Exception) { "{}" }

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

        const val CAP_CAPTURE = "capture"
        const val CAP_ROTATE = "rotate"
        const val CAP_PIN_EXIT = "pin-exit"
        const val CAP_CONTROLS = "controls"
        const val CAP_KIOSK_LOCK = "kiosk-lock"
        const val CAP_AGENT = "agent"
        const val CAP_BRIGHTNESS = "brightness"      // the app sets the real backlight; the page draws no dimming layer

        /**
         * Capabilities: settings and capture requests arrive through the port, so
         * without it the shell can do neither. TVs never rotate natively; the page
         * rotates with CSS there. TVs do not report brightness either: a TV box
         * (NVIDIA Shield) sends HDMI and has no backlight to set, so the page draws
         * its darkening layer.
         */
        fun caps(portAttached: Boolean, isTv: Boolean): List<String> = buildList {
            if (portAttached) {
                add(CAP_PIN_EXIT)
                add(CAP_CAPTURE)
                if (!isTv) add(CAP_ROTATE)
                add(CAP_CONTROLS)
                add(CAP_KIOSK_LOCK)
                add(CAP_AGENT)
                if (!isTv) add(CAP_BRIGHTNESS)   // a TV box has no backlight (HDMI): the page darkens the picture there
            }
        }

        /**
         * The JSON returned by stats(). Negative values are left out. [lock] is one of
         * off, locked, unlocked, pinned, owner, failed (left out when null).
         */
        fun statsJson(uptimeS: Long, cacheFiles: Int, cacheBytes: Long, freeBytes: Long, lock: String? = null, agent: JSONObject? = null): String {
            val o = JSONObject().put("uptime_s", maxOf(0L, uptimeS))
                .put("cache", JSONObject().put("files", maxOf(0, cacheFiles)).put("bytes", maxOf(0L, cacheBytes)))
            if (freeBytes >= 0) o.put("storage_free_bytes", freeBytes)
            if (lock != null) o.put("lock", lock.take(16))
            if (agent != null) o.put("agent", agent)
            return o.toString()
        }

        fun forThisDevice(
            clientId: String,
            adminUrl: String,
            caps: List<String>,
            stats: () -> String,
        ): ShellInfo = ShellInfo(
            version = BuildConfig.VERSION_NAME,
            device = deviceName(Build.MANUFACTURER, Build.MODEL, Build.VERSION.RELEASE),
            clientId = clientId,
            adminUrl = adminUrl,
            caps = caps,
            stats = stats,
        )
    }
}
