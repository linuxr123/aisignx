package com.aisignx.player.shell

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import java.security.MessageDigest

/**
 * What the agent must remember across a process restart: the switches from the last manifest
 * (so an offline start still runs the watchdog), handled command ids (at most once), and the
 * restart times (hourly caps, restarts_1h), and for signed commands: the server's pinned public
 * key, the highest accepted sequence number (both per server origin), this display's id, the last
 * reboot and a pending-update mark. Holds no secrets (a public key is not one). Wall-clock times, because the
 * monotonic clock restarts with the device.
 */
class AgentStore(ctx: Context) {
    private val p: SharedPreferences =
        ctx.applicationContext.getSharedPreferences("agent_state", Context.MODE_PRIVATE)

    var settings: AgentSettings
        get() = AgentSettings(
            p.getBoolean("watchdog", false), p.getBoolean("restart", false), p.getBoolean("logs", false),
            p.getBoolean("reboot", false), p.getBoolean("startup", false), p.getBoolean("update", false),
        )
        set(s) {
            p.edit().putBoolean("watchdog", s.watchdog).putBoolean("restart", s.restart).putBoolean("logs", s.logs)
                .putBoolean("reboot", s.reboot).putBoolean("startup", s.startup).putBoolean("update", s.update).apply()
        }

    // ---- signed commands ---------------------------------------------------------------

    /** This display's id from the last manifest (0: not known yet). */
    var displayId: Long
        get() = p.getLong("display_id", 0L)
        set(v) { if (v != displayId) p.edit().putLong("display_id", v).commit() }

    /** The command key pinned for [origin] (32 raw bytes), or null. */
    fun pinnedKey(origin: String): ByteArray? {
        val text = p.getString("pin_" + tag(origin), null) ?: return null
        return try { Base64.decode(text, Base64.NO_WRAP).takeIf { it.size == Ed25519.KEY_BYTES } } catch (_: IllegalArgumentException) { null }
    }

    @Synchronized fun pinKey(origin: String, key: ByteArray) {
        p.edit().putString("pin_" + tag(origin), Base64.encodeToString(key, Base64.NO_WRAP)).commit()
    }

    /** The server presented a key other than the pinned one (reported as command_key_changed). */
    fun keyChanged(origin: String): Boolean = p.getBoolean("kc_" + tag(origin), false)
    fun setKeyChanged(origin: String, changed: Boolean) {
        if (changed != keyChanged(origin)) p.edit().putBoolean("kc_" + tag(origin), changed).commit()
    }

    /**
     * "Forget server key" and re-pair: drops every pin, changed flag, sequence number and server-time mark,
     * so a new key (or a restored server) starts a fresh sequence instead of being refused as "replayed".
     */
    @Synchronized fun forgetKeyPins() {
        val e = p.edit()
        for (k in p.all.keys) if (TierRules.forgottenOnRepair(k)) e.remove(k)
        e.commit()
    }

    /**
     * Records a response's server_time_ms. False (and nothing stored) when it is lower than the highest
     * one seen for [origin]: a real server clock does not go backwards, so the response is not trusted.
     */
    @Synchronized fun noteServerTime(origin: String, ms: Long): Boolean {
        val high = p.getLong("st_" + tag(origin), 0L)
        if (!TierRules.serverTimeOk(high, ms)) return false
        if (ms > high) p.edit().putLong("st_" + tag(origin), ms).commit()
        return true
    }

    fun lastSeq(origin: String): Long = p.getLong("seq_" + tag(origin), 0L)

    /** Written synchronously BEFORE the command runs, so it runs at most once even if the device crashes. */
    @Synchronized fun saveSeq(origin: String, seq: Long) {
        if (seq > lastSeq(origin)) p.edit().putLong("seq_" + tag(origin), seq).commit()
    }

    var lastRebootMs: Long
        get() = p.getLong("last_reboot", 0L)
        set(v) { p.edit().putLong("last_reboot", v).commit() }

    /** Why the last update_app install failed ("version: reason|time"), reported in the next heartbeat. */
    var updateError: String?
        get() = p.getString("update_error", null)
        set(v) { p.edit().apply { if (v == null) remove("update_error") else putString("update_error", v) }.commit() }

    /** The version an update_app install is for (so a later failure can name it). */
    var updateVersion: String
        get() = p.getString("update_version", "") .orEmpty()
        set(v) { p.edit().putString("update_version", v).commit() }

    /** Set before an update is handed to the installer; the app starts itself once it was replaced. */
    var updatePendingMs: Long
        get() = p.getLong("update_pending", 0L)
        set(v) { p.edit().putLong("update_pending", v).commit() }

    private fun tag(origin: String): String =
        MessageDigest.getInstance("SHA-256").digest(origin.toByteArray()).joinToString("") { "%02x".format(it) }.take(12)

    @Synchronized fun isHandled(id: String): Boolean = id in handled()

    /** Written synchronously BEFORE the command runs: a crash mid-command must not repeat it. */
    @Synchronized fun markHandled(id: String) {
        val ids = AgentRates.rememberId(handled(), id)
        p.edit().putString("handled", ids.joinToString(",")).commit()
    }

    fun handledSet(): Set<String> = handled().toSet()

    private fun handled(): List<String> =
        p.getString("handled", "").orEmpty().split(',').filter { AgentCommands.validId(it) }

    /** Heavy watchdog steps (activity recreate, process restart): the hourly cap. */
    fun heavyTimes(): List<Long> = AgentRates.parseTimes(p.getString("heavy", null))
    @Synchronized fun addHeavy(now: Long) { p.edit().putString("heavy", AgentRates.formatTimes(AgentRates.add(heavyTimes(), now))).commit() }

    /** Every restart of the player this agent caused (watchdog, crash, command). */
    fun restartTimes(): List<Long> = AgentRates.parseTimes(p.getString("restarts", null))
    @Synchronized fun addRestart(now: Long) {
        p.edit().putString("restarts", AgentRates.formatTimes(AgentRates.add(restartTimes(), now))).putLong("last_restart", now).commit()
    }

    val lastRestartMs: Long get() = p.getLong("last_restart", 0L)

    var lastCrashRestartMs: Long
        get() = p.getLong("crash_restart", 0L)
        set(v) { p.edit().putLong("crash_restart", v).commit() }

    var gaveUpMs: Long
        get() = p.getLong("gave_up", 0L)
        set(v) { p.edit().putLong("gave_up", v).commit() }
}
