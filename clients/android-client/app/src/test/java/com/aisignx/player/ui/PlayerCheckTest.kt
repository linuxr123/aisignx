package com.aisignx.player.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayerCheckTest {

    @Test fun runningOnlyWhenPlayerSetAState() {
        assertTrue(PlayerCheck.isRunning("\"playing\""))
        assertTrue(PlayerCheck.isRunning("\"connecting\""))
        // Error page or a snapshot whose scripts never ran.
        assertFalse(PlayerCheck.isRunning("\"\""))
        assertFalse(PlayerCheck.isRunning("null"))
        assertFalse(PlayerCheck.isRunning(null))
        assertFalse(PlayerCheck.isRunning(""))
    }

    @Test fun anyServerErrorFailsTheMainFrame() {
        for (code in listOf(500, 502, 503, 504, 520, 524, 530)) assertTrue("$code", PlayerCheck.isServerError(code))
        for (code in listOf(200, 304, 404, 499)) assertFalse("$code", PlayerCheck.isServerError(code))
    }
}
