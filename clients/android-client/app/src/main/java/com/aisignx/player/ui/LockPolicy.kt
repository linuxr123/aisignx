package com.aisignx.player.ui

/**
 * The decisions behind Lock player, without Android calls so they can be unit tested.
 * [clock] is a monotonic millisecond clock (SystemClock.elapsedRealtime in the app).
 *
 * A remote unlock lives in memory only: a restart locks again. Request IDs of the last
 * [MAX_SEEN] commands are remembered, so a command delivered twice acts once.
 */
class LockPolicy(private val clock: () -> Long) {

    enum class Step { NONE, START, STOP }

    @Volatile var kioskLock = false
        private set

    @Volatile private var unlockedUntil = 0L
    private val seen = ArrayDeque<String>()

    /** Without Device Owner each start shows the pin prompt, so it is tried once per lock period. */
    private var pinTried = false

    /** Returns true when the setting changed. */
    fun configure(lock: Boolean): Boolean {
        val changed = lock != kioskLock
        kioskLock = lock
        return changed
    }

    /** Lock task is wanted: Lock player is on and no remote unlock is running. */
    fun want(): Boolean = kioskLock && clock() >= unlockedUntil

    /** Lock player is on but lifted by a remote unlock; the hidden exit skips the PIN. */
    fun remoteUnlocked(): Boolean = kioskLock && clock() < unlockedUntil

    /** Milliseconds until a remote unlock ends, 0 when none is running. */
    fun remainingMs(): Long = maxOf(0L, unlockedUntil - clock())

    /** Lift the lock for [minutes] (clamped to 1..240). False for a repeated request ID. */
    fun unlockFor(minutes: Int, requestId: String): Boolean {
        if (!firstSeen(requestId)) return false
        unlockedUntil = clock() + minutes.coerceIn(MIN_MINUTES, MAX_MINUTES) * 60_000L
        return true
    }

    /** End a remote unlock now. False for a repeated request ID. */
    fun relock(requestId: String): Boolean {
        if (!firstSeen(requestId)) return false
        unlockedUntil = 0L
        return true
    }

    /** What to do now, given the device's lock task state. */
    fun step(inLockTask: Boolean, owner: Boolean, canStart: Boolean = true): Step {
        if (!want()) {
            pinTried = false
            return if (inLockTask) Step.STOP else Step.NONE
        }
        if (inLockTask || !canStart) return Step.NONE
        if (owner) return Step.START
        if (pinTried) return Step.NONE
        pinTried = true
        return Step.START
    }

    /** For stats().lock: off, unlocked, failed, owner, pinned or locked (wanted, not active). */
    fun state(inLockTask: Boolean, owner: Boolean, failed: Boolean): String = when {
        !kioskLock -> "off"
        remoteUnlocked() -> "unlocked"
        failed -> "failed"
        inLockTask -> if (owner) "owner" else "pinned"
        else -> "locked"
    }

    private fun firstSeen(id: String): Boolean {
        if (id in seen) return false
        seen.addLast(id)
        while (seen.size > MAX_SEEN) seen.removeFirst()
        return true
    }

    companion object {
        const val MIN_MINUTES = 1
        const val MAX_MINUTES = 240
        const val MAX_SEEN = 20
    }
}
