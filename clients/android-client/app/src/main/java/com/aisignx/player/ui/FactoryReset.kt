package com.aisignx.player.ui

/** Rules of the Setup screen's factory reset button (pure, unit tested). */
object FactoryReset {
    const val WORD = "RESET"

    /** The button is for the Device Owner only: nobody else can wipe the device from an app. */
    fun offered(owner: Boolean): Boolean = owner

    /** Second step: the exact word, upper case; surrounding spaces from the keyboard are ignored. */
    fun confirmed(typed: String?): Boolean = typed?.trim() == WORD
}
