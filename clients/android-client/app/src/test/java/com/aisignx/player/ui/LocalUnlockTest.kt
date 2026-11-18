package com.aisignx.player.ui

import com.aisignx.player.ui.Lockdown.Mode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalUnlockTest {
    private val now = 1_700_000_000_000L
    private val min = 60_000L

    @Test fun windowIsTenMinutes() {
        assertEquals(now + 10 * min, LocalUnlock.endFrom(now))
    }

    @Test fun openUntilTheEnd() {
        val until = LocalUnlock.endFrom(now)
        assertTrue(LocalUnlock.isOpen(until, now))
        assertTrue(LocalUnlock.isOpen(until, now + 10 * min - 1))
        assertFalse(LocalUnlock.isOpen(until, now + 10 * min))
        assertFalse(LocalUnlock.isOpen(until, now + 11 * min))
        assertEquals(0L, LocalUnlock.remainingMs(until, now + 11 * min))
    }

    @Test fun neverStartedIsClosed() {
        assertFalse(LocalUnlock.isOpen(0L, now))
        assertEquals(0L, LocalUnlock.remainingMs(0L, now))
    }

    @Test fun aClockSetBackDoesNotKeepItOpen() {
        val until = LocalUnlock.endFrom(now)
        assertFalse("end is more than a window away", LocalUnlock.isOpen(until, now - 5 * min))
    }

    @Test fun minutesRoundUp() {
        assertEquals(0, LocalUnlock.minutesLeft(0))
        assertEquals(1, LocalUnlock.minutesLeft(1))
        assertEquals(1, LocalUnlock.minutesLeft(min))
        assertEquals(2, LocalUnlock.minutesLeft(min + 1))
        assertEquals(10, LocalUnlock.minutesLeft(10 * min))
    }

    @Test fun setupScreenHoldsEvenWithoutAWindow() {
        assertTrue(LocalUnlock.hold(0L, now, setupInFront = true))
        assertFalse(LocalUnlock.hold(0L, now, setupInFront = false))
        assertTrue(LocalUnlock.hold(LocalUnlock.endFrom(now), now, setupInFront = false))
        assertFalse(LocalUnlock.hold(LocalUnlock.endFrom(now), now + 11 * min, setupInFront = false))
    }

    @Test fun holdKeepsTheLockdownReleased() {
        assertEquals(Mode.RELEASE, Lockdown.decide(owner = true, kioskLock = true, remoteUnlocked = false, localHold = true))
        assertEquals(Mode.ENGAGE, Lockdown.decide(owner = true, kioskLock = true, remoteUnlocked = false, localHold = false))
        assertEquals(Mode.NONE, Lockdown.decide(owner = false, kioskLock = true, remoteUnlocked = false, localHold = true))
        assertEquals(Mode.RELEASE, Lockdown.decide(owner = true, kioskLock = false, remoteUnlocked = false, localHold = true))
    }

    @Test fun lockPolicyDoesNotWantLockTaskWhileHeld() {
        val p = LockPolicy { now }
        p.configure(true)
        assertTrue(p.want())
        p.hold(true)
        assertFalse(p.want())
        assertEquals(LockPolicy.Step.NONE, p.step(inLockTask = false, owner = true))
        assertEquals(LockPolicy.Step.STOP, p.step(inLockTask = true, owner = true))
        assertEquals("unlocked", p.state(false, true, false))
        p.hold(false)
        assertTrue(p.want())
        assertEquals(LockPolicy.Step.START, p.step(inLockTask = false, owner = true))
    }
}
