package com.aisignx.player.shell

import com.aisignx.player.Config
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ShellInfoTest {

    @Test fun infoJsonFields() {
        val id = Config.newClientId()
        val json = JSONObject(ShellInfo("3.0.0", "Sony BRAVIA / Android 12", id, "https://signage.lan").info())
        assertEquals("android", json.getString("name"))
        assertEquals("3.0.0", json.getString("version"))
        assertEquals("Sony BRAVIA / Android 12", json.getString("device"))
        assertEquals(id, json.getString("client_id"))
        assertEquals("https://signage.lan", json.getString("admin_url"))
        assertEquals(setOf("name", "version", "device", "client_id", "admin_url", "caps"), json.keySet())
        assertEquals(0, json.getJSONArray("caps").length())
    }

    @Test fun capsList() {
        assertEquals(
            listOf("pin-exit", "capture", "rotate", "controls", "kiosk-lock", "agent", "brightness"),
            ShellInfo.caps(portAttached = true, isTv = false),
        )
        assertEquals(listOf("pin-exit", "capture", "controls", "kiosk-lock", "agent", "brightness"), ShellInfo.caps(portAttached = true, isTv = true))
        assertEquals(emptyList<String>(), ShellInfo.caps(portAttached = false, isTv = false))
        val caps = ShellInfo.caps(portAttached = true, isTv = false)
        val json = JSONObject(ShellInfo("3.0.0", "d", Config.newClientId(), "https://s", caps).info())
        val arr = json.getJSONArray("caps")
        assertEquals(caps, List(arr.length()) { arr.getString(it) })
    }

    @Test fun statsJson() {
        val o = JSONObject(ShellInfo.statsJson(3600, 12, 123_456_789L, 52_000_000_000L))
        assertEquals(3600L, o.getLong("uptime_s"))
        assertEquals(12, o.getJSONObject("cache").getInt("files"))
        assertEquals(123_456_789L, o.getJSONObject("cache").getLong("bytes"))
        assertEquals(52_000_000_000L, o.getLong("storage_free_bytes"))
        assertEquals(setOf("uptime_s", "cache", "storage_free_bytes"), o.keySet())
        assertFalse(JSONObject(ShellInfo.statsJson(-1, -1, -1, -1)).has("storage_free_bytes"))
    }

    @Test fun statsLock() {
        assertEquals("owner", JSONObject(ShellInfo.statsJson(1, 0, 0, 0, "owner")).getString("lock"))
        assertFalse(JSONObject(ShellInfo.statsJson(1, 0, 0, 0)).has("lock"))
        assertEquals(16, JSONObject(ShellInfo.statsJson(1, 0, 0, 0, "x".repeat(40))).getString("lock").length)
    }

    @Test fun statsNeverThrows() {
        val info = ShellInfo("3.0.0", "d", Config.newClientId(), "https://s", stats = { error("boom") })
        assertEquals("{}", info.stats())
        val ok = ShellInfo("3.0.0", "d", Config.newClientId(), "https://s", stats = { """{"a":1}""" })
        assertEquals("""{"a":1}""", ok.stats())
    }

    @Test fun clientIdFormat() {
        val a = Config.newClientId()
        val b = Config.newClientId()
        assertTrue(a, Regex("^[0-9a-f]{32}$").matches(a))
        assertTrue(Config.CLIENT_ID_RE.matches(b))
        assertNotEquals(a, b)
    }

    @Test fun deviceName() {
        assertEquals("NVIDIA SHIELD Android TV / Android 11",
            ShellInfo.deviceName("NVIDIA", "SHIELD Android TV", "11"))
        assertEquals("Xiaomi MIBOX4 / Android 9", ShellInfo.deviceName("Xiaomi", "MIBOX4", "9"))
        assertEquals("Google Pixel 7 / Android 14", ShellInfo.deviceName("Google", "Google Pixel 7", "14"))
        assertEquals("Android device / Android ?", ShellInfo.deviceName(null, null, null))
        assertEquals(120, ShellInfo.deviceName("M", "x".repeat(300), "14").length)
    }
}
