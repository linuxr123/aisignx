package com.aisignx.player.shell

/**
 * The run modes of set_startup on Android, as plain Kotlin without Android calls (JVM unit tests).
 *
 * The server shows three run modes, the same as on Windows: Application, Application with auto-start,
 * Shell. Here "shell" means this app is the Home app: the device boots into it and the Home button
 * returns to it. That is the "Use as Home app" choice of the Setup screen ([com.aisignx.player.Config.homeMode]),
 * so the command and the checkbox read and write the same value.
 */
object StartupRules {
    const val MODE_APP = "app"
    const val MODE_SHELL = "shell"

    /** Shown as the result of a refused "app" request while Lock player holds the Home app. */
    const val LOCK_KEEPS_HOME = "Lock player is on: it keeps this app as the Home app. Turn Lock player off first"

    sealed class Decision {
        /** Run the request; [homeMode] is the new Home mode, or null when no mode was requested. */
        data class Apply(val homeMode: Boolean?) : Decision()
        /** Ack "failed" with [result]; nothing at all is changed (not even autostart). */
        data class Refuse(val result: String) : Decision()
    }

    /**
     * [lockKeepsHome]: Device Owner with Lock player on, so the lockdown keeps the app as the persistent Home
     * app whatever Home mode says. "shell" is then already true and is accepted; "app" cannot be honoured and
     * is refused out loud instead of being ignored.
     */
    fun decide(mode: String?, lockKeepsHome: Boolean): Decision = when (mode) {
        null -> Decision.Apply(null)
        MODE_SHELL -> Decision.Apply(true)
        MODE_APP -> if (lockKeepsHome) Decision.Refuse(LOCK_KEEPS_HOME) else Decision.Apply(false)
        else -> Decision.Refuse("bad_params")
    }

    /** What diag.agent.startup.mode says: "shell" while this app is the Home app (Home mode, or the lockdown engaged). */
    fun reportedMode(homeMode: Boolean, lockdownEngaged: Boolean): String =
        if (homeMode || lockdownEngaged) MODE_SHELL else MODE_APP

    /** The ack text of a finished command. */
    fun doneText(autostart: Boolean, mode: String): String = "autostart ${if (autostart) "on" else "off"}, mode $mode"
}
