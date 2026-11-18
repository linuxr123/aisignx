package com.aisignx.player.ui

import android.provider.Settings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsPagesTest {
    private fun page(id: String) = SettingsPages.ALL.first { it.id == id }

    @Test fun pagesAndOrder() {
        assertEquals(listOf("wifi", "display", "date", "sound", "apps", "about", "main"), SettingsPages.ALL.map { it.id })
        assertEquals(listOf(Settings.ACTION_SETTINGS), SettingsPages.ALL.last().actions)
        assertTrue(Settings.ACTION_DEVICE_INFO_SETTINGS in page("about").actions)
    }

    @Test fun openedPageTriesNothingElse() {
        val tried = mutableListOf<String>()
        val r = SettingsPages.open(page("display")) { tried += it; true }
        assertEquals(SettingsPages.Result.OPENED, r)
        assertEquals(listOf(Settings.ACTION_DISPLAY_SETTINGS), tried)
    }

    @Test fun anExceptionFallsBackToTheMainPage() {
        val tried = mutableListOf<String>()
        val r = SettingsPages.open(page("sound")) { tried += it; if (it == Settings.ACTION_SETTINGS) true else throw SecurityException("no") }
        assertEquals(SettingsPages.Result.FALLBACK, r)
        assertEquals(listOf(Settings.ACTION_SOUND_SETTINGS, Settings.ACTION_SETTINGS), tried)
    }

    @Test fun appsTriesItsSecondActionBeforeTheMainPage() {
        val tried = mutableListOf<String>()
        val r = SettingsPages.open(page("apps")) { tried += it; it == Settings.ACTION_MANAGE_ALL_APPLICATIONS_SETTINGS }
        assertEquals(SettingsPages.Result.OPENED, r)
        assertEquals(2, tried.size)
    }

    @Test fun everythingFailingIsReportedOnceForMain() {
        val tried = mutableListOf<String>()
        assertEquals(SettingsPages.Result.FAILED, SettingsPages.open(page("main")) { tried += it; false })
        assertEquals("main page is not retried as its own fallback", 1, tried.size)
        assertEquals(SettingsPages.Result.FAILED, SettingsPages.open(page("wifi")) { false })
    }
}
