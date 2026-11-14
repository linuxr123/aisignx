package com.aisignx.player.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LockPolicyTest {

    private var now = 1_000_000L
    private val p = LockPolicy { now }
    private val rid = "0123456789abcdef"

    @Test fun offByDefault() {
        assertFalse(p.want())
        assertEquals(LockPolicy.Step.NONE, p.step(inLockTask = false, owner = true))
        assertEquals(LockPolicy.Step.STOP, p.step(inLockTask = true, owner = true))
        assertEquals("off", p.state(false, false, false))
    }

    @Test fun configure() {
        assertTrue(p.configure(true))
        assertFalse(p.configure(true))
        assertTrue(p.want())
        assertTrue(p.configure(false))
        assertFalse(p.want())
    }

    @Test fun ownerStartsEveryTime() {
        p.configure(true)
        assertEquals(LockPolicy.Step.START, p.step(false, owner = true))
        assertEquals(LockPolicy.Step.START, p.step(false, owner = true))
        assertEquals(LockPolicy.Step.NONE, p.step(true, owner = true))
        assertEquals(LockPolicy.Step.NONE, p.step(false, owner = true, canStart = false))
    }

    @Test fun pinningPromptsOncePerLockPeriod() {
        p.configure(true)
        assertEquals(LockPolicy.Step.START, p.step(false, owner = false))
        assertEquals(LockPolicy.Step.NONE, p.step(false, owner = false)) // declined: no nagging
        p.configure(false)
        assertEquals(LockPolicy.Step.NONE, p.step(false, owner = false))
        p.configure(true)
        assertEquals(LockPolicy.Step.START, p.step(false, owner = false))
    }

    @Test fun unlockExpires() {
        p.configure(true)
        assertTrue(p.unlockFor(15, rid))
        assertFalse(p.want())
        assertTrue(p.remoteUnlocked())
        assertEquals(15 * 60_000L, p.remainingMs())
        assertEquals(LockPolicy.Step.STOP, p.step(true, owner = true))
        assertEquals("unlocked", p.state(false, true, false))
        now += 15 * 60_000L - 1
        assertFalse(p.want())
        now += 1
        assertTrue(p.want())
        assertFalse(p.remoteUnlocked())
        assertEquals(0L, p.remainingMs())
        assertEquals(LockPolicy.Step.START, p.step(false, owner = true))
    }

    @Test fun clamp() {
        p.configure(true)
        p.unlockFor(9999, "aaaaaaaaaaaaaaaa")
        assertEquals(240 * 60_000L, p.remainingMs())
        p.unlockFor(0, "bbbbbbbbbbbbbbbb")
        assertEquals(60_000L, p.remainingMs())
    }

    @Test fun dedupeAndRelock() {
        p.configure(true)
        assertTrue(p.unlockFor(5, rid))
        now += 60_000L
        assertFalse(p.unlockFor(5, rid)) // repeated delivery does not extend
        assertEquals(4 * 60_000L, p.remainingMs())
        assertTrue(p.relock("1111111111111111"))
        assertTrue(p.want())
        assertFalse(p.relock("1111111111111111"))
        // Only the last 20 IDs are kept.
        for (i in 0 until LockPolicy.MAX_SEEN) p.relock("%016x".format(i))
        assertTrue(p.unlockFor(5, rid))
    }

    @Test fun unlockWithoutLockDoesNotBypass() {
        assertTrue(p.unlockFor(15, rid))
        assertFalse(p.remoteUnlocked())
        assertEquals("off", p.state(false, false, false))
    }

    @Test fun states() {
        p.configure(true)
        assertEquals("owner", p.state(true, owner = true, failed = false))
        assertEquals("pinned", p.state(true, owner = false, failed = false))
        assertEquals("failed", p.state(false, owner = false, failed = true))
        assertEquals("locked", p.state(false, owner = false, failed = false))
    }
}
