package com.aisignx.player.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShellPortTest {

    @Test fun captureReplyOk() {
        val o = JSONObject(ShellPort.captureReply(7, "/9j/4AAQ"))
        assertEquals("capture", o.getString("t"))
        assertEquals(7, o.getInt("id"))
        assertTrue(o.getBoolean("ok"))
        assertEquals("/9j/4AAQ", o.getString("jpeg"))
    }

    @Test fun captureReplyFailed() {
        val o = JSONObject(ShellPort.captureReply(1_000_000_000, null))
        assertFalse(o.getBoolean("ok"))
        assertEquals(setOf("t", "id", "ok"), o.keySet())
    }

    @Test fun captureScale() {
        assertEquals(1920 to 1080, Capture.scaled(1920, 1080))
        assertEquals(1920 to 1080, Capture.scaled(3840, 2160))
        assertEquals(1080 to 1920, Capture.scaled(2160, 3840))
        assertEquals(720 to 1280, Capture.scaled(720, 1280))
        assertEquals(1920 to 1, Capture.scaled(10000, 2))
    }
}
