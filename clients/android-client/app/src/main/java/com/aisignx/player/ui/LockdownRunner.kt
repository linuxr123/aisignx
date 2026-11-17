package com.aisignx.player.ui

/**
 * The bookkeeping around [Lockdown], without Android calls (JVM unit tests). [LockdownApplier] gives
 * it the real device policy calls and preferences.
 *
 * What it guarantees:
 * - RELEASE is never held back by a failed ENGAGE: each mode has its own failure time.
 * - [releaseNow] (PIN exit, Remove Device Owner) ignores every gate.
 * - A release that failed is remembered ([State.releasePending]) so something else can retry it.
 * - The full release runs only when the lockdown was engaged ([State.engaged]); otherwise only the
 *   cheap Home alias step, so policy the admin set another way is not wiped on every boot.
 * - The crash-loop breaker ([State.tripped]) turns every ENGAGE into a RELEASE until a manifest has
 *   been fetched successfully.
 */
class LockdownRunner(private val state: State, private val now: () -> Long) {

    /** What must survive a process restart. */
    interface State {
        /** The lockdown was started (maybe only partly) and has not been fully released since. */
        var engaged: Boolean
        /** A release failed and must be tried again. */
        var releasePending: Boolean
        /** The crash-loop breaker fired: stay released until a manifest has been fetched. */
        var tripped: Boolean
    }

    @Volatile private var applied: Lockdown.Mode? = null
    private val failedAt = HashMap<Lockdown.Mode, Long>()

    val tripped: Boolean get() = state.tripped
    val releasePending: Boolean get() = state.releasePending

    /** The mode that is actually run for [requested] (the breaker overrides ENGAGE). */
    fun effective(requested: Lockdown.Mode): Lockdown.Mode =
        if (requested == Lockdown.Mode.ENGAGE && state.tripped) Lockdown.Mode.RELEASE else requested

    /**
     * Applies [requested] unless it is NONE, already applied in this process, or in its own back-off.
     * Returns null when nothing was run, else the failed steps (empty: done).
     */
    @Synchronized
    fun sync(ops: Lockdown.Ops, requested: Lockdown.Mode, homeMode: Boolean): List<String>? {
        if (requested == Lockdown.Mode.NONE) return null
        val mode = effective(requested)
        if (mode == applied) return null
        if (!Lockdown.retryAllowed(failedAt[mode] ?: 0L, now())) return null
        return run(ops, mode, homeMode, forceFull = false)
    }

    /** Release now, whatever happened before (PIN exit, remote removal of the owner). [full]: even if never engaged. */
    @Synchronized
    fun releaseNow(ops: Lockdown.Ops, homeMode: Boolean, full: Boolean): List<String> =
        run(ops, Lockdown.Mode.RELEASE, homeMode, forceFull = full)

    /** The retry timer fired: release again when the last release failed. Returns null when nothing was pending. */
    @Synchronized
    fun retryPendingRelease(ops: Lockdown.Ops, homeMode: Boolean): List<String>? {
        if (!state.releasePending) return null
        return run(ops, Lockdown.Mode.RELEASE, homeMode, forceFull = false)
    }

    /** Breaker fired: stay released until [manifestFetched]. */
    @Synchronized
    fun trip() {
        state.tripped = true
        applied = null // the next sync releases, whatever ran before
    }

    /** A manifest came from the server: the app is healthy, the lockdown may engage again. */
    @Synchronized
    fun manifestFetched() {
        if (state.tripped) {
            state.tripped = false
            applied = null // the next sync (ENGAGE) really runs
        }
    }

    private fun run(ops: Lockdown.Ops, mode: Lockdown.Mode, homeMode: Boolean, forceFull: Boolean): List<String> {
        val failed: List<String>
        if (mode == Lockdown.Mode.ENGAGE) {
            state.engaged = true // before the first call: a half-done engage must still be released
            state.releasePending = false
            failed = Lockdown.engage(ops)
        } else {
            failed = if (state.engaged || forceFull) Lockdown.release(ops, homeMode) else Lockdown.releaseLight(ops, homeMode)
        }
        if (failed.isEmpty()) {
            applied = mode
            failedAt.remove(mode)
            if (mode == Lockdown.Mode.RELEASE) { state.engaged = false; state.releasePending = false }
        } else {
            failedAt[mode] = now()
            if (mode == Lockdown.Mode.RELEASE) state.releasePending = true
            else applied = null
        }
        return failed
    }
}

/**
 * Crash-loop breaker. If the player process starts [MAX_STARTS] times within [WINDOW_MS] without a
 * manifest having been fetched by a process that lived at least [HEALTHY_UPTIME_MS], the whole
 * lockdown is released (restrictions, persistent Home), because a broken build as persistent Home with
 * no way to reach Settings would otherwise leave only the hardware recovery menu.
 */
object CrashLoop {
    const val MAX_STARTS = 5
    const val WINDOW_MS = 10 * 60_000L
    const val HEALTHY_UPTIME_MS = 60_000L

    /** The start times to keep after a start at [now]: recent ones only (a clock set back drops the future ones). */
    fun record(starts: List<Long>, now: Long): List<Long> =
        (starts.filter { it <= now && now - it <= WINDOW_MS } + now).takeLast(MAX_STARTS)

    fun tripped(starts: List<Long>, now: Long): Boolean =
        starts.count { it <= now && now - it <= WINDOW_MS } >= MAX_STARTS

    /** A manifest only proves health when this process has lived long enough to have used the app. */
    fun healthy(processUptimeMs: Long): Boolean = processUptimeMs >= HEALTHY_UPTIME_MS

    fun parse(text: String?): List<Long> =
        text.orEmpty().split(',').mapNotNull { it.toLongOrNull() }.takeLast(MAX_STARTS)

    fun format(starts: List<Long>): String = starts.joinToString(",")
}
