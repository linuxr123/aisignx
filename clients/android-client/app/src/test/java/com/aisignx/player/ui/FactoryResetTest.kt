package com.aisignx.player.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FactoryResetTest {
    @Test fun onlyForTheDeviceOwner() {
        assertTrue(FactoryReset.offered(true))
        assertFalse(FactoryReset.offered(false))
    }

    @Test fun theWordMustMatchExactly() {
        assertTrue(FactoryReset.confirmed("RESET"))
        assertTrue(FactoryReset.confirmed("  RESET "))
        assertFalse(FactoryReset.confirmed("reset"))
        assertFalse(FactoryReset.confirmed("RESET now"))
        assertFalse(FactoryReset.confirmed(""))
        assertFalse(FactoryReset.confirmed(null))
    }
}
