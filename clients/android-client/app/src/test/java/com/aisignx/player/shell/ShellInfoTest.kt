package com.aisignx.player.shell

import com.aisignx.player.Config
import org.json.JSONObject
import org.junit.Assert.assertEquals
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
        assertEquals(setOf("name", "version", "device", "client_id", "admin_url"), json.keySet())
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
