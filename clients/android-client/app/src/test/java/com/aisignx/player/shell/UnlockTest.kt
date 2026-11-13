package com.aisignx.player.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UnlockTest {

    private val salt = "000102030405060708090a0b0c0d0e0f"
    private val hash2468 = "37cff31b6ad22b037e276408739e6702ab1a83eca9924fef485e1089b3c3ce2d"
    private val hash12345678 = "42b99a197a7d9cafa9fa5b60091da410edb6f3c1cc4e159a5f901b5348abb716"

    @Test fun sharedVectors() {
        assertTrue(Unlock.verify("2468", UnlockHash(200_000, salt, hash2468)))
        assertTrue(Unlock.verify("12345678", UnlockHash(1000, salt, hash12345678)))
    }

    @Test fun wrongPin() {
        val h = UnlockHash(1000, salt, hash12345678)
        assertFalse(Unlock.verify("12345679", h))
        assertFalse(Unlock.verify("", h))
        assertFalse(Unlock.verify("12a45678", h))
        assertFalse(Unlock.verify("1".repeat(13), h))
        assertFalse(Unlock.verify("2468", UnlockHash(1000, salt, hash2468)))
    }

    @Test fun waitSchedule() {
        for (f in 0..4) assertEquals(0L, Unlock.waitMs(f))
        assertEquals(60_000L, Unlock.waitMs(5))
        assertEquals(120_000L, Unlock.waitMs(6))
        assertEquals(240_000L, Unlock.waitMs(7))
        assertEquals(480_000L, Unlock.waitMs(8))
        assertEquals(900_000L, Unlock.waitMs(9))
        assertEquals(900_000L, Unlock.waitMs(100))
        assertEquals(900_000L, Unlock.waitMs(Int.MAX_VALUE))
    }

    @Test fun prefRoundTrip() {
        val d = "$"
        val h = UnlockHash(200_000, salt, hash2468)
        assertEquals("200000$d$salt$d$hash2468", h.toPref())
        assertEquals(h, UnlockHash.fromPref(h.toPref()))
        assertNull(UnlockHash.fromPref(null))
        assertNull(UnlockHash.fromPref(""))
        assertNull(UnlockHash.fromPref("200000$d$salt"))
        assertNull(UnlockHash.fromPref("1000$d$salt$d$hash2468")) // iter below the minimum
        assertNull(UnlockHash.fromPref("200000$d${salt.uppercase()}$d$hash2468"))
        assertNull(UnlockHash.fromPref("x$d$salt$d$hash2468"))
    }

    @Test fun ofValidates() {
        assertNull(UnlockHash.of(9_999, salt, hash2468))
        assertNull(UnlockHash.of(1_000_001, salt, hash2468))
        assertNull(UnlockHash.of(200_000, salt.dropLast(2), hash2468))
        assertNull(UnlockHash.of(200_000, salt, hash2468 + "00"))
        assertEquals(UnlockHash(10_000, salt, hash2468), UnlockHash.of(10_000, salt, hash2468))
    }
}
