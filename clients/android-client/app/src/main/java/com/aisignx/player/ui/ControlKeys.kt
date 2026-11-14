package com.aisignx.player.ui

import android.view.KeyEvent

/**
 * Keys and remote buttons forwarded to the player page as control actions while the
 * display allows input (docs/player-api.md, "Player controls"). The D-pad mirrors the
 * keyboard arrows. DPAD_CENTER is not here: PlayerActivity turns its short press into
 * "toggle" and keeps the long press for the hidden exit.
 */
object ControlKeys {

    const val TOGGLE = "toggle"
    const val PLAY = "play"
    const val PAUSE = "pause"
    const val FORWARD = "forward"
    const val BACK = "back"
    const val NEXT = "next"
    const val PREV = "prev"

    /** The seven actions the page accepts. */
    val ACTIONS = setOf(TOGGLE, PLAY, PAUSE, FORWARD, BACK, NEXT, PREV)

    /**
     * The action for [keyCode], or null when the key is not a control key.
     * [fromMouse] marks the mouse's side "back" button, which arrives as KEYCODE_BACK;
     * the real Back key is never forwarded.
     */
    fun action(keyCode: Int, fromMouse: Boolean = false): String? = when (keyCode) {
        KeyEvent.KEYCODE_DPAD_RIGHT -> FORWARD
        KeyEvent.KEYCODE_DPAD_LEFT -> BACK
        KeyEvent.KEYCODE_DPAD_UP -> PREV
        KeyEvent.KEYCODE_DPAD_DOWN -> NEXT
        KeyEvent.KEYCODE_ENTER,
        KeyEvent.KEYCODE_NUMPAD_ENTER,
        KeyEvent.KEYCODE_SPACE,
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
        KeyEvent.KEYCODE_HEADSETHOOK -> TOGGLE
        KeyEvent.KEYCODE_MEDIA_PLAY -> PLAY
        KeyEvent.KEYCODE_MEDIA_PAUSE,
        KeyEvent.KEYCODE_MEDIA_STOP -> PAUSE
        KeyEvent.KEYCODE_MEDIA_NEXT,
        KeyEvent.KEYCODE_CHANNEL_DOWN,
        KeyEvent.KEYCODE_PAGE_DOWN -> NEXT
        KeyEvent.KEYCODE_MEDIA_PREVIOUS,
        KeyEvent.KEYCODE_CHANNEL_UP,
        KeyEvent.KEYCODE_PAGE_UP -> PREV
        KeyEvent.KEYCODE_MEDIA_FAST_FORWARD,
        KeyEvent.KEYCODE_MEDIA_SKIP_FORWARD,
        KeyEvent.KEYCODE_MEDIA_STEP_FORWARD -> FORWARD
        KeyEvent.KEYCODE_MEDIA_REWIND,
        KeyEvent.KEYCODE_MEDIA_SKIP_BACKWARD,
        KeyEvent.KEYCODE_MEDIA_STEP_BACKWARD -> BACK
        KeyEvent.KEYCODE_BACK -> if (fromMouse) PREV else null
        KeyEvent.KEYCODE_FORWARD -> NEXT
        else -> null
    }
}
