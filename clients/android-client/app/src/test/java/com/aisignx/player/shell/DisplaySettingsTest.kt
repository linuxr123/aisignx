package com.aisignx.player.shell

import android.content.pm.ActivityInfo
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DisplaySettingsTest {

    private val salt = "000102030405060708090a0b0c0d0e0f"
    private val hash = "37cff31b6ad22b037e276408739e6702ab1a83eca9924fef485e1089b3c3ce2d"
    private val unlock = """{"alg":"pbkdf2-sha256","iter":200000,"salt":"$salt","hash":"$hash"}"""

    private fun manifest(settings: String?): String {
        val extra = if (settings != null) ""","settings":$settings""" else ""
        return """{"display":{"id":1,"name":"Lobby"},"items":[]$extra}"""
    }

    @Test fun parsesFullSettings() {
        val s = DisplaySettings.parse(
            manifest("""{"orientation":270,"volume":40,"show_offline_banner":true,"allow_input":true,"unlock":$unlock}"""),
        )!!
        assertEquals(270, s.orientation)
        assertTrue(s.allowInput)
        assertEquals(UnlockHash(200_000, salt, hash), s.unlock)
    }

    @Test fun defaults() {
        val s = DisplaySettings.parse(manifest("""{"unlock":null}"""))!!
        assertEquals(0, s.orientation)
        assertFalse(s.allowInput)
        assertNull(s.unlock)
    }

    @Test fun missingSettingsIsNull() {
        assertNull(DisplaySettings.parse(manifest(null)))
        assertNull(DisplaySettings.parse(manifest("null")))
        assertNull(DisplaySettings.parse("not json"))
        assertNull(DisplaySettings.parse("[]"))
    }

    @Test fun clampsBadValues() {
        for (bad in listOf("45", "-90", "360", "\"90\"", "90.5", "null")) {
            val s = DisplaySettings.parse(manifest("""{"orientation":$bad,"allow_input":"yes"}"""))!!
            assertEquals(bad, 0, s.orientation)
            assertFalse(s.allowInput)
        }
    }

    @Test fun badUnlockIsNoPin() {
        val bad = listOf(
            """{"alg":"sha1","iter":200000,"salt":"$salt","hash":"$hash"}""",
            """{"alg":"pbkdf2-sha256","iter":100,"salt":"$salt","hash":"$hash"}""",
            """{"alg":"pbkdf2-sha256","iter":"200000","salt":"$salt","hash":"$hash"}""",
            """{"alg":"pbkdf2-sha256","iter":200000,"salt":"zz","hash":"$hash"}""",
            """{"alg":"pbkdf2-sha256","iter":200000,"salt":"$salt"}""",
            "\"2468\"",
        )
        for (u in bad) assertNull(u, DisplaySettings.parse(manifest("""{"unlock":$u}"""))!!.unlock)
        assertNull(DisplaySettings.parseUnlock(null))
        assertEquals(UnlockHash(200_000, salt, hash), DisplaySettings.parseUnlock(JSONObject(unlock)))
    }

    @Test fun orientationMapping() {
        // Clockwise like the CSS rotation: on a landscape panel REVERSE_PORTRAIT is
        // Surface.ROTATION_90 and PORTRAIT is ROTATION_270 (counter-clockwise).
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, DisplaySettings.orientationFlag(0))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_REVERSE_PORTRAIT, DisplaySettings.orientationFlag(90))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_REVERSE_LANDSCAPE, DisplaySettings.orientationFlag(180))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT, DisplaySettings.orientationFlag(270))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, DisplaySettings.orientationFlag(42))
    }
}
