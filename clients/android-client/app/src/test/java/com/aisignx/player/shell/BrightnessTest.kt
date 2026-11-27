package com.aisignx.player.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant

class BrightnessTest {
    private fun at(iso: String) = Instant.parse(iso).toEpochMilli()
    private val night = Brightness(80, Brightness.Dim(30, 22 * 60, 6 * 60, "America/New_York"))

    @Test fun `the base level shows outside the night window and the night level inside, across midnight`() {
        assertEquals(30, night.levelAt(at("2026-01-15T04:00:00Z")))    // 23:00 in New York
        assertEquals(30, night.levelAt(at("2026-01-15T10:00:00Z")))    // 05:00
        assertEquals(80, night.levelAt(at("2026-01-15T17:00:00Z")))    // 12:00
        assertEquals(80, night.levelAt(at("2026-01-15T02:59:00Z")))    // 21:59: the window starts at 22:00
        assertEquals(30, night.levelAt(at("2026-01-15T03:00:00Z")))
        assertEquals(30, night.levelAt(at("2026-01-15T10:59:00Z")))    // 05:59: it ends at 06:00
        assertEquals(80, night.levelAt(at("2026-01-15T11:00:00Z")))
    }

    @Test fun `night never makes the screen brighter than the base level`() {
        val b = Brightness(20, Brightness.Dim(60, 0, 23 * 60 + 59, "UTC"))
        assertEquals(20, b.levelAt(at("2026-01-15T13:00:00Z")))
    }

    @Test fun `parse reads the manifest settings and ignores bad values`() {
        val b = Brightness.parse(JSONObject("""{"brightness": 55, "dim": {"level": 25, "from": "22:00", "to": "06:30", "tz": "Europe/Paris"}}"""))
        assertEquals(55, b.base)
        assertEquals(Brightness.Dim(25, 1320, 390, "Europe/Paris"), b.dim)
        assertEquals(Brightness.DEFAULT, Brightness.parse(null))
        assertEquals(100, Brightness.parse(JSONObject("""{"brightness": "x"}""")).base)
        assertEquals(10, Brightness.parse(JSONObject("""{"brightness": 3}""")).base)
        assertNull(Brightness.parse(JSONObject("""{"brightness": 70, "dim": {"level": 30, "from": "25:00", "to": "06:00"}}""")).dim)
        assertNull(Brightness.parse(JSONObject("""{"brightness": 70, "dim": {"level": 30, "from": "22:00", "to": "22:00"}}""")).dim)
        assertNull(Brightness.parse(JSONObject("""{"brightness": 70, "dim": "x"}""")).dim)
    }

    @Test fun `the window value is the system setting at 100 percent and never fully dark`() {
        assertEquals(-1f, Brightness.windowValue(100), 0f)
        assertEquals(0.5f, Brightness.windowValue(50), 0.001f)
        assertEquals(0.1f, Brightness.windowValue(1), 0.001f)
    }

    @Test fun `the system value matches the device's brightness slider, full at 100 and never dark`() {
        assertEquals(255, Brightness.systemValue(100))
        assertEquals(21, Brightness.systemValue(50))      // the slider's halfway point is 1/12 of full
        assertEquals(87, Brightness.systemValue(80))
        assertEquals(1, Brightness.systemValue(10))
        assertEquals(1, Brightness.systemValue(1))
        val values = (10..100).map { Brightness.systemValue(it) }
        assertEquals(values.sorted(), values)             // brighter never gives a lower value
    }

    @Test fun `the saved form survives a round trip`() {
        assertEquals(night, Brightness.fromPref(Brightness.toPref(night)))
        assertEquals(Brightness.DEFAULT, Brightness.fromPref(null))
        assertEquals(Brightness.DEFAULT, Brightness.fromPref("not json"))
    }
}
