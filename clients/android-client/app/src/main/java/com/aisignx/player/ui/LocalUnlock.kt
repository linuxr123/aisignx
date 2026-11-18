package com.aisignx.player.ui

/**
 * The local unlock window, without Android calls (JVM unit tests). After a correct PIN exit the
 * Device Owner lockdown stays released for [WINDOW_MS], so the person at the tablet can finish in
 * Settings and the Setup screen. The end time is a wall-clock value (it must survive a reboot).
 * [LocalUnlockWindow] stores it and feeds [Lockdown.decide].
 */
object LocalUnlock {
    const val WINDOW_MS = 10 * 60_000L

    fun endFrom(now: Long): Long = now + WINDOW_MS

    /**
     * Milliseconds left. 0 when none was started, when it is over, or when the stored end is further
     * away than a window can be (the clock was set back: never trust a window that long).
     */
    fun remainingMs(until: Long, now: Long): Long {
        if (until <= 0L) return 0L
        val left = until - now
        return if (left in 1..WINDOW_MS) left else 0L
    }

    fun isOpen(until: Long, now: Long): Boolean = remainingMs(until, now) > 0L

    /** Whole minutes to show, rounded up (so "1" until the last second). */
    fun minutesLeft(ms: Long): Int = if (ms <= 0L) 0 else ((ms + 59_999L) / 60_000L).toInt()

    /** The lockdown is held released: the window is open, or the Setup screen is in front. */
    fun hold(until: Long, now: Long, setupInFront: Boolean): Boolean = setupInFront || isOpen(until, now)
}
