package com.aisignx.player.shell

import org.json.JSONObject
import java.util.Calendar
import java.util.TimeZone

/**
 * The screen's brightness from the manifest: a base level (percent) and an optional night window in the organization's time zone.
 * Pure rules (no Android calls) so they are unit tested; PlayerActivity applies the result to the window's real backlight.
 * Twin of brightnessFor() in static/player/device.js.
 */
data class Brightness(val base: Int, val dim: Dim?) {
    data class Dim(val level: Int, val fromMin: Int, val toMin: Int, val tz: String)

    /** The level (10..100) to show at [nowMs]: the base, or the night level inside the window (which may cross midnight). */
    fun levelAt(nowMs: Long): Int {
        val d = dim ?: return base
        val cal = Calendar.getInstance(zone(d.tz)).apply { timeInMillis = nowMs }
        val now = cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE)
        val inside = if (d.fromMin < d.toMin) now >= d.fromMin && now < d.toMin else now >= d.fromMin || now < d.toMin
        return if (inside) minOf(base, d.level) else base
    }

    companion object {
        val DEFAULT = Brightness(100, null)
        private val CLOCK = Regex("^([01]\\d|2[0-3]):([0-5]\\d)$")

        private fun zone(name: String): TimeZone = if (name.isEmpty()) TimeZone.getDefault() else TimeZone.getTimeZone(name)   // unknown names give GMT, never an error
        private fun pct(v: Any?, fallback: Int): Int = (v as? Number)?.toDouble()?.takeIf { it.isFinite() }?.let { Math.round(it).toInt().coerceIn(10, 100) } ?: fallback
        private fun minutes(t: String): Int = CLOCK.matchEntire(t)!!.groupValues.let { it[1].toInt() * 60 + it[2].toInt() }

        /** From the manifest's "settings" object; bad or missing values mean full brightness and no window. */
        fun parse(settings: JSONObject?): Brightness {
            if (settings == null) return DEFAULT
            val base = pct(settings.opt("brightness"), 100)
            val d = settings.optJSONObject("dim") ?: return Brightness(base, null)
            val from = d.optString("from"); val to = d.optString("to")
            if (!CLOCK.matches(from) || !CLOCK.matches(to) || from == to) return Brightness(base, null)
            return Brightness(base, Dim(pct(d.opt("level"), 100), minutes(from), minutes(to), d.optString("tz")))
        }

        /** The value for WindowManager.LayoutParams.screenBrightness: -1 (the system's own setting) at 100 %, else 0.1..0.99. */
        fun windowValue(level: Int): Float = if (level >= 100) -1f else (level / 100f).coerceIn(0.1f, 0.99f)

        /**
         * Settings.System.SCREEN_BRIGHTNESS (1..255) for a level, so the device's own brightness slider shows the same
         * percent. The slider is perceptual: Android maps it to the linear 0..255 value with the HLG curve
         * (BrightnessUtils.convertGammaToLinear), so 50 % on the slider is about 21/255, not 128. 100 % is 255.
         */
        fun systemValue(level: Int): Int {
            val v = level.coerceIn(10, 100) / 100.0
            val linear = if (v <= 0.5) v * v / 3.0 else (Math.exp((v - HLG_C) / HLG_A) + HLG_B) / 12.0
            return Math.round(linear * 255).toInt().coerceIn(1, 255)
        }
        private const val HLG_A = 0.17883277
        private const val HLG_B = 0.28466892
        private const val HLG_C = 0.55991073

        fun toPref(b: Brightness): String = JSONObject().put("brightness", b.base).apply {
            b.dim?.let { put("dim", JSONObject().put("level", it.level).put("from", "%02d:%02d".format(it.fromMin / 60, it.fromMin % 60))
                .put("to", "%02d:%02d".format(it.toMin / 60, it.toMin % 60)).put("tz", it.tz)) }
        }.toString()

        fun fromPref(text: String?): Brightness {
            if (text == null) return DEFAULT
            return try { parse(JSONObject(text)) } catch (_: Exception) { DEFAULT }
        }
    }
}
