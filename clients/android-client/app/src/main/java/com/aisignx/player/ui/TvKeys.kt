package com.aisignx.player.ui

import android.view.KeyEvent

/**
 * Remote-control keys the player swallows, so a viewer cannot pause or seek a
 * <video>, scroll the page or leave the player. Forwarding keys to the player page
 * is deferred. DPAD_CENTER and MENU long presses are handled by PlayerActivity
 * (hidden exit to setup).
 */
object TvKeys {

    private val CONSUMED = setOf(
        KeyEvent.KEYCODE_BACK,
        KeyEvent.KEYCODE_ESCAPE,
        KeyEvent.KEYCODE_DPAD_CENTER,
        KeyEvent.KEYCODE_DPAD_UP,
        KeyEvent.KEYCODE_DPAD_DOWN,
        KeyEvent.KEYCODE_DPAD_LEFT,
        KeyEvent.KEYCODE_DPAD_RIGHT,
        KeyEvent.KEYCODE_ENTER,
        KeyEvent.KEYCODE_NUMPAD_ENTER,
        KeyEvent.KEYCODE_SPACE,
        KeyEvent.KEYCODE_TAB,
        KeyEvent.KEYCODE_MENU,
        KeyEvent.KEYCODE_INFO,
        KeyEvent.KEYCODE_GUIDE,
        KeyEvent.KEYCODE_SEARCH,
        KeyEvent.KEYCODE_PAGE_UP,
        KeyEvent.KEYCODE_PAGE_DOWN,
        KeyEvent.KEYCODE_MOVE_HOME,
        KeyEvent.KEYCODE_MOVE_END,
        KeyEvent.KEYCODE_CHANNEL_UP,
        KeyEvent.KEYCODE_CHANNEL_DOWN,
        KeyEvent.KEYCODE_LAST_CHANNEL,
        KeyEvent.KEYCODE_MEDIA_PLAY,
        KeyEvent.KEYCODE_MEDIA_PAUSE,
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
        KeyEvent.KEYCODE_MEDIA_STOP,
        KeyEvent.KEYCODE_MEDIA_NEXT,
        KeyEvent.KEYCODE_MEDIA_PREVIOUS,
        KeyEvent.KEYCODE_MEDIA_FAST_FORWARD,
        KeyEvent.KEYCODE_MEDIA_REWIND,
        KeyEvent.KEYCODE_MEDIA_SKIP_FORWARD,
        KeyEvent.KEYCODE_MEDIA_SKIP_BACKWARD,
        KeyEvent.KEYCODE_MEDIA_STEP_FORWARD,
        KeyEvent.KEYCODE_MEDIA_STEP_BACKWARD,
        KeyEvent.KEYCODE_MEDIA_RECORD,
        KeyEvent.KEYCODE_MEDIA_AUDIO_TRACK,
        KeyEvent.KEYCODE_CAPTIONS,
        KeyEvent.KEYCODE_PROG_RED,
        KeyEvent.KEYCODE_PROG_GREEN,
        KeyEvent.KEYCODE_PROG_YELLOW,
        KeyEvent.KEYCODE_PROG_BLUE,
    )

    /** Whether the player consumes [keyCode]. Volume, power and Home pass through. */
    fun consumes(keyCode: Int): Boolean =
        keyCode in CONSUMED ||
            keyCode in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 ||
            keyCode in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9

    /** Keys whose 1.5 s long press opens the exit-to-setup dialog. */
    fun isExitKey(keyCode: Int): Boolean =
        keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_MENU
}
