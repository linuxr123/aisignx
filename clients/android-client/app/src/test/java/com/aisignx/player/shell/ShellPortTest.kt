package com.aisignx.player.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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

    @Test fun controlMessage() {
        val o = JSONObject(ShellPort.controlMessage("next", false))
        assertEquals(setOf("t", "a", "r"), o.keySet())
        assertEquals("control", o.getString("t"))
        assertEquals("next", o.getString("a"))
        assertFalse(o.getBoolean("r"))
        assertTrue(JSONObject(ShellPort.controlMessage("forward", true)).getBoolean("r"))
    }

    private val rid = "0123456789abcdef"

    private fun cmd(json: String) = ShellPort.parseCommand(JSONObject(json))

    @Test fun parseUnlock() {
        assertEquals(
            ShellPort.Command("unlock", 15, rid),
            cmd("""{"action":"unlock","minutes":15,"request_id":"$rid"}"""),
        )
        assertEquals(240, cmd("""{"action":"unlock","minutes":9999,"request_id":"$rid"}""")!!.minutes)
        assertEquals(1, cmd("""{"action":"unlock","minutes":0,"request_id":"$rid"}""")!!.minutes)
        assertEquals(1, cmd("""{"action":"unlock","minutes":-5,"request_id":"$rid"}""")!!.minutes)
    }

    @Test fun parseLock() {
        assertEquals(ShellPort.Command("lock", 0, rid), cmd("""{"action":"lock","request_id":"$rid"}"""))
    }

    @Test fun parseRejects() {
        val bad = listOf(
            """{"action":"unlock","minutes":15}""",
            """{"action":"unlock","minutes":"15","request_id":"$rid"}""",
            """{"action":"unlock","minutes":1.5,"request_id":"$rid"}""",
            """{"action":"unlock","request_id":"$rid"}""",
            """{"action":"unlock","minutes":15,"request_id":"0123456789ABCDEF"}""",
            """{"action":"unlock","minutes":15,"request_id":"0123"}""",
            """{"action":"lock","request_id":12}""",
            """{"action":"reload","request_id":"$rid"}""",
            """{"request_id":"$rid"}""",
        )
        for (b in bad) assertNull(b, cmd(b))
    }

    @Test fun captureScale() {
        assertEquals(1920 to 1080, Capture.scaled(1920, 1080))
        assertEquals(1920 to 1080, Capture.scaled(3840, 2160))
        assertEquals(1080 to 1920, Capture.scaled(2160, 3840))
        assertEquals(720 to 1280, Capture.scaled(720, 1280))
        assertEquals(1920 to 1, Capture.scaled(10000, 2))
    }
}
