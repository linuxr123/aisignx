package com.aisignx.player.ui

import android.content.Context
import com.aisignx.player.AiSignXApp

/**
 * "Use as Home app": one code path for the Setup checkbox and the set_startup command (mode shell/app).
 */
object HomeMode {
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
