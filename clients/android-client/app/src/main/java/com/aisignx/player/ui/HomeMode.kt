package com.aisignx.player.ui

import android.content.Context
import com.aisignx.player.AiSignXApp

/**
 * "Use as Home app": one code path for the Setup checkbox and the set_startup command (mode shell/app).
 */
object HomeMode {
    /**
     * Lock player on a device that is NOT Device Owner is only Android's screen pinning, which on a TV box or a phone
     * does not reliably stop the Home button. The dependable way is for this app to be the Home app: Home then returns to
     * the player, and leaving needs the unlock PIN. So Lock player turns Home mode on there (once; not during the
     * PIN unlock window, and never against the user's own later choice while the window is open).
     */
    fun lockNeedsHome(owner: Boolean, kioskLock: Boolean, unlockHold: Boolean, homeMode: Boolean): Boolean =
        !owner && kioskLock && !unlockHold && !homeMode

    /** Device Owner with Lock player on: the lockdown keeps this app as the persistent Home app. */
    fun lockKeepsHome(ctx: Context): Boolean =
        LockTask.isDeviceOwner(ctx) && AiSignXApp.from(ctx).config.kioskLock

    /** The app is the Home app right now: the user's choice, or the lockdown is engaged. */
    fun active(ctx: Context): Boolean = AiSignXApp.from(ctx).config.homeMode || LockdownApplier.engaged(ctx)

    /**
     * Stores the choice and enables or disables the Home alias. When Lock player holds the Home app the alias
     * stays enabled (the player sets it again on its next start). Switching off as Device Owner also removes
     * the persistent Home preference an earlier release left behind. Returns false when a step failed.
     */
    fun apply(ctx: Context, enabled: Boolean): Boolean {
        val config = AiSignXApp.from(ctx).config
        config.homeMode = enabled
        if (!enabled && lockKeepsHome(ctx)) return true
        var ok = LockTask.trySetHomeAlias(ctx, enabled)
        if (!enabled && LockTask.isDeviceOwner(ctx)) ok = LockdownApplier.clearPersistentHome(ctx) && ok
        return ok
    }
}
