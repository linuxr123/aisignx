package com.aisignx.player.shell

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.zip.GZIPOutputStream

/**
 * The device agent's decisions, as plain Kotlin without Android calls (JVM unit tests).
 * Agent.kt does the I/O around them. Nothing here ever takes a path, URL or command line
 * from the server: the verb list is closed.
 */

/**
 * The admin's switches from manifest.settings.agent. Everything is off by default.
 * [reboot], [startup] and [update] allow the signed (Tier B) commands.
 */
data class AgentSettings(
    val watchdog: Boolean,
    val restart: Boolean,
    val logs: Boolean,
    val reboot: Boolean = false,
    val startup: Boolean = false,
    val update: Boolean = false,
) {
    val polls: Boolean get() = restart || logs || reboot || startup || update

    companion object {
        val OFF = AgentSettings(false, false, false)

        /**
         * Null when the manifest has no "settings" object (leave the stored switches alone).
         * Settings without "agent" (an older server) mean everything off. Only a JSON true counts.
         */
        fun parse(manifestJson: String): AgentSettings? {
            val root = try { JSONObject(manifestJson) } catch (_: Exception) { return null }
            val s = root.optJSONObject("settings") ?: return null
            val a = s.optJSONObject("agent") ?: return OFF
            return AgentSettings(
                a.opt("watchdog") == true, a.opt("restart") == true, a.opt("logs") == true,
                a.opt("reboot") == true, a.opt("startup") == true, a.opt("update") == true,
            )
        }
    }
}

/** Closed list of verbs and the strict check of one command object from GET /commands. */
object AgentCommands {
    const val RESTART = "restart_player"
    const val LOGS = "collect_logs"
    const val MAX_PER_POLL = 5
    private val ID_RE = Regex("^[0-9a-f]{16}$")

    sealed class Verdict {
        /** Run [verb]; the id is already validated and not handled before. */
        data class Run(val id: String, val verb: String) : Verdict()
        /** Answer with ack "failed" and [result]; nothing runs. */
        data class Refuse(val id: String, val result: String) : Verdict()
        /** Malformed or already handled: do nothing at all (a bad id cannot even be acked). */
        object Ignore : Verdict()
    }

    fun validId(id: Any?): Boolean = id is String && ID_RE.matches(id)

    fun check(c: Any?, s: AgentSettings, handled: Set<String>): Verdict {
        val o = c as? JSONObject ?: return Verdict.Ignore
        val id = o.opt("id") as? String ?: return Verdict.Ignore
        if (!validId(id) || id in handled) return Verdict.Ignore
        val ttl = o.opt("expires_in_s")
        if (ttl is Number && ttl.toDouble() <= 0.0) return Verdict.Refuse(id, "expired")
        return when (o.opt("action")) {
            RESTART -> if (s.restart) Verdict.Run(id, RESTART) else Verdict.Refuse(id, "switched off")
            LOGS -> if (s.logs) Verdict.Run(id, LOGS) else Verdict.Refuse(id, "switched off")
            else -> Verdict.Refuse(id, "unsupported")
        }
    }

    /** The server's clock from the same response as the commands, or null. */
    fun serverTimeMs(body: String): Long? = try {
        SignedCommand.integer(JSONObject(body).opt("server_time_ms"))
    } catch (_: Exception) { null }

    /** Parses {"commands":[...]}; at most [MAX_PER_POLL] entries, anything else is empty. */
    fun parseList(body: String): List<Any?> {
        val arr = try { JSONObject(body).optJSONArray("commands") } catch (_: Exception) { null } ?: return emptyList()
        return List(minOf(arr.length(), MAX_PER_POLL)) { arr.opt(it) }
    }
}

/** The escalation ladder for a page that stopped sending "alive". */
object Watchdog {
    const val GRACE_MS = 60_000L        // after a page load nothing is judged
    const val RELOAD_MS = 45_000L       // level 1
    const val WEBVIEW_MS = 90_000L      // level 2
    const val ACTIVITY_MS = 180_000L    // level 3 (heavy)
    const val PROCESS_MS = 300_000L     // level 4 (heavy)
    const val HEAVY_WINDOW_MS = 3_600_000L
    const val HEAVY_PER_WINDOW = 1      // levels 3 and 4 together, at most once an hour

    enum class Step { NONE, RELOAD_PAGE, RECREATE_WEBVIEW, RECREATE_ACTIVITY, RESTART_PROCESS }

    /**
     * [step] to run now (NONE when idle). [level] is the ladder level the silence has reached;
     * the caller stores it as "done" so a level is acted on once per silent stretch.
     * [gaveUp]: the silence asks for a heavy step but the hourly cap is used up.
     */
    data class Decision(val step: Step, val level: Int, val gaveUp: Boolean)

    private val IDLE = Decision(Step.NONE, 0, false)

    /**
     * All times are one monotonic clock in ms. [lastAliveMs] is the last "alive", or the moment the
     * silence clock was reset (page load, resume, screen on). [pageLoadedMs] is the last page load.
     * [doneLevel] is the highest level already acted on since the last alive.
     * [heavyInWindow] counts level 3/4 actions in the last hour (kept across process restarts).
     */
    fun decide(nowMs: Long, lastAliveMs: Long, pageLoadedMs: Long, doneLevel: Int, heavyInWindow: Int): Decision {
        if (nowMs - pageLoadedMs < GRACE_MS) return IDLE
        val silent = nowMs - lastAliveMs
        val level = when {
            silent >= PROCESS_MS -> 4
            silent >= ACTIVITY_MS -> 3
            silent >= WEBVIEW_MS -> 2
            silent >= RELOAD_MS -> 1
            else -> 0
        }
        if (level == 0 || level <= doneLevel) return IDLE
        if (level >= 3 && heavyInWindow >= HEAVY_PER_WINDOW) return Decision(Step.NONE, level, true)
        return Decision(Step.values()[level], level, false)
    }
}

/** Small helpers for the persisted restart times (wall clock ms) and rate limits. */
object AgentRates {
    const val HOUR_MS = 3_600_000L
    const val CRASH_RESTART_GAP_MS = 10 * 60_000L
    private const val KEEP = 20

    /** A time in the future (the clock jumped back) still counts as recent: the safe side. */
    fun recent(times: List<Long>, nowMs: Long, windowMs: Long = HOUR_MS): Int =
        times.count { nowMs - it < windowMs }

    fun parseTimes(s: String?): List<Long> =
        s.orEmpty().split(',').mapNotNull { it.trim().toLongOrNull() }.takeLast(KEEP)

    fun formatTimes(times: List<Long>): String = times.takeLast(KEEP).joinToString(",")

    fun add(times: List<Long>, nowMs: Long): List<Long> = (times + nowMs).takeLast(KEEP)

    /** At most one crash-triggered restart per 10 minutes. */
    fun crashRestartAllowed(lastMs: Long, nowMs: Long): Boolean =
        lastMs <= 0L || nowMs - lastMs >= CRASH_RESTART_GAP_MS || lastMs - nowMs > CRASH_RESTART_GAP_MS

    /** Handled command ids, newest last, bounded. */
    fun rememberId(ids: List<String>, id: String, max: Int = 64): List<String> = (ids + id).takeLast(max)
}

/** Redaction and size limit of the log the agent uploads. */
object LogPrep {
    const val MAX_GZIP = 900 * 1024

    private val RULES: List<Pair<Regex, String>> = listOf(
        Regex("(?i)bearer\\s+[A-Za-z0-9._~+/=-]+") to "Bearer [redacted]",
        Regex("(?i)\\b(set-cookie|cookie)\\b\\s*[:=].*") to "$1: [redacted]",
        Regex("(?i)aisignx_display\\s*=\\s*[^;\\s\"']+") to "aisignx_display=[redacted]",
        Regex(
            "(?i)([\"']?[\\w-]*(?:token|secret|password|passwd|certpin|cert_pin|pin|hash|salt|cookie|unlock|authorization)" +
                "[\\w-]*[\"']?\\s*[:=]\\s*)([\"']?)[^\\s,;\"'}&]+",
        ) to "$1$2[redacted]",
        Regex("\\b[0-9a-fA-F]{64}\\b") to "[redacted-hex]",
        Regex("(?<![\\w/.-])[A-Za-z0-9_-]{43}(?![\\w/.-])") to "[redacted-token]",
    )

    /** [secrets] are known values (token, certificate pin) removed wherever they appear. */
    fun redact(text: String, secrets: List<String> = emptyList()): String {
        var s = text
        for (secret in secrets) if (secret.length >= 8) s = s.replace(secret, "[redacted]")
        for ((re, rep) in RULES) s = re.replace(s, rep)
        return s
    }

    private fun gzip(s: String): ByteArray {
        val out = ByteArrayOutputStream()
        GZIPOutputStream(out).use { it.write(s.toByteArray(Charsets.UTF_8)) }
        return out.toByteArray()
    }

    /** The newest part of [text] whose gzip is at most [maxBytes], cut at a line start. */
    fun gzipNewest(text: String, maxBytes: Int = MAX_GZIP): ByteArray {
        var s = text
        while (true) {
            val gz = gzip(s)
            if (gz.size <= maxBytes || s.isEmpty()) return gz
            var cut = s.length - s.length * 3 / 4
            val nl = s.indexOf('\n', cut)
            if (nl in 0 until s.length - 1) cut = nl + 1
            s = s.substring(cut)
        }
    }

    private val TOKEN_RE = Regex("^[A-Za-z0-9_-]{16,128}$")

    /** The display token out of the WebView's cookie header, or null. Never log the result. */
    fun tokenFromCookies(header: String?): String? {
        for (part in header.orEmpty().split(';')) {
            val i = part.indexOf('=')
            if (i <= 0 || part.substring(0, i).trim() != "aisignx_display") continue
            val v = part.substring(i + 1).trim()
            return v.takeIf { TOKEN_RE.matches(it) }
        }
        return null
    }
}
