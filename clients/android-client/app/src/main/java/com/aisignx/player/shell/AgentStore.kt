package com.aisignx.player.shell

import android.content.Context
import android.content.SharedPreferences

/**
 * What the agent must remember across a process restart: the switches from the last manifest
 * (so an offline start still runs the watchdog), handled command ids (at most once), and the
 * restart times (hourly caps, restarts_1h). Holds no secrets. Wall-clock times, because the
 * monotonic clock restarts with the device.
 */
class AgentStore(ctx: Context) {
    private val p: SharedPreferences =
        ctx.applicationContext.getSharedPreferences("agent_state", Context.MODE_PRIVATE)

    var settings: AgentSettings
        get() = AgentSettings(p.getBoolean("watchdog", false), p.getBoolean("restart", false), p.getBoolean("logs", false))
        set(s) { p.edit().putBoolean("watchdog", s.watchdog).putBoolean("restart", s.restart).putBoolean("logs", s.logs).apply() }

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
