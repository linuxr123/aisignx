package com.aisignx.player.ui

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ControlKeysTest {

    @Test fun mapping() {
        val table = mapOf(
            KeyEvent.KEYCODE_DPAD_RIGHT to "forward",
            KeyEvent.KEYCODE_DPAD_LEFT to "back",
            KeyEvent.KEYCODE_DPAD_UP to "prev",
            KeyEvent.KEYCODE_DPAD_DOWN to "next",
            KeyEvent.KEYCODE_ENTER to "toggle",
            KeyEvent.KEYCODE_NUMPAD_ENTER to "toggle",
            KeyEvent.KEYCODE_SPACE to "toggle",
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE to "toggle",
            KeyEvent.KEYCODE_HEADSETHOOK to "toggle",
            KeyEvent.KEYCODE_MEDIA_PLAY to "play",
            KeyEvent.KEYCODE_MEDIA_PAUSE to "pause",
            KeyEvent.KEYCODE_MEDIA_STOP to "pause",
            KeyEvent.KEYCODE_MEDIA_NEXT to "next",
            KeyEvent.KEYCODE_CHANNEL_DOWN to "next",
            KeyEvent.KEYCODE_PAGE_DOWN to "next",
            KeyEvent.KEYCODE_MEDIA_PREVIOUS to "prev",
            KeyEvent.KEYCODE_CHANNEL_UP to "prev",
            KeyEvent.KEYCODE_PAGE_UP to "prev",
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD to "forward",
            KeyEvent.KEYCODE_MEDIA_SKIP_FORWARD to "forward",
            KeyEvent.KEYCODE_MEDIA_STEP_FORWARD to "forward",
            KeyEvent.KEYCODE_MEDIA_REWIND to "back",
            KeyEvent.KEYCODE_MEDIA_SKIP_BACKWARD to "back",
            KeyEvent.KEYCODE_MEDIA_STEP_BACKWARD to "back",
            KeyEvent.KEYCODE_FORWARD to "next",
        )
        for ((code, action) in table) {
            assertEquals("key $code", action, ControlKeys.action(code))
            assertTrue(action in ControlKeys.ACTIONS)
        }
        assertEquals(7, ControlKeys.ACTIONS.size)
    }

    @Test fun backOnlyFromMouse() {
        assertNull(ControlKeys.action(KeyEvent.KEYCODE_BACK))
        assertEquals("prev", ControlKeys.action(KeyEvent.KEYCODE_BACK, fromMouse = true))
    }

    @Test fun notControlKeys() {
        for (code in listOf(
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_MENU, KeyEvent.KEYCODE_HOME,
            KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN, KeyEvent.KEYCODE_POWER,
            KeyEvent.KEYCODE_ESCAPE, KeyEvent.KEYCODE_TAB, KeyEvent.KEYCODE_1, KeyEvent.KEYCODE_A,
        )) assertNull("key $code", ControlKeys.action(code))
    }
}

class BackKeyTest {
    @org.junit.Test
    fun backLeavesOnlyWhenLockPlayerIsOffAndItIsNotTheMouseButton() {
        org.junit.Assert.assertTrue(TvKeys.backLeaves(lockPlayer = false, fromMouse = false))
        org.junit.Assert.assertFalse(TvKeys.backLeaves(lockPlayer = true, fromMouse = false))
        org.junit.Assert.assertFalse(TvKeys.backLeaves(lockPlayer = false, fromMouse = true))
        org.junit.Assert.assertFalse(TvKeys.backLeaves(lockPlayer = true, fromMouse = true))
    }
}
