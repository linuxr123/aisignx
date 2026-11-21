package com.aisignx.player.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeModeTest {
    @Test
    fun `lock player without device owner turns the home app on once`() {
        assertTrue(HomeMode.lockNeedsHome(owner = false, kioskLock = true, unlockHold = false, homeMode = false))
        assertFalse(HomeMode.lockNeedsHome(owner = false, kioskLock = true, unlockHold = false, homeMode = true))
    }

    @Test
    fun `nothing changes when the lock is off, during the pin unlock window or for a device owner`() {
        assertFalse(HomeMode.lockNeedsHome(owner = false, kioskLock = false, unlockHold = false, homeMode = false))
        assertFalse(HomeMode.lockNeedsHome(owner = false, kioskLock = true, unlockHold = true, homeMode = false))
        assertFalse(HomeMode.lockNeedsHome(owner = true, kioskLock = true, unlockHold = false, homeMode = false))
    }
}
