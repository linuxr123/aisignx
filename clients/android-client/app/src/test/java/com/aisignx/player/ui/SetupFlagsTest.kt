package com.aisignx.player.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class SetupFlagsTest {
    @Test fun describesValuesAndMissingSettings() {
        assertEquals("user_setup_complete=0 device_provisioned=1", SetupFlags.describe(0, 1))
        assertEquals("user_setup_complete=unset device_provisioned=unset", SetupFlags.describe(null, null))
    }
}
