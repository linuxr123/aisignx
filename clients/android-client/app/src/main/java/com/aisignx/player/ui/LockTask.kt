package com.aisignx.player.ui

import android.app.Activity
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import com.aisignx.player.AisignxDeviceAdminReceiver
import com.aisignx.player.FileLog

/**
 * Android lock task for Lock player. Without Device Owner, startLockTask() is screen
 * pinning: Android asks the viewer once and Back+Overview unpins. As Device Owner the
 * app allow-lists itself, so lock task starts without a prompt, Home, Recents and the
 * status bar are gone, and the app is the persistent Home app. See docs/kiosk-lockdown.md.
 */
object LockTask {

    private const val TAG = "LockTask"

    /** Class name stays com.aisignx.player.* even with the .debug applicationId suffix. */
    const val HOME_ALIAS = "com.aisignx.player.HomeAlias"

    fun admin(ctx: Context): ComponentName = ComponentName(ctx, AisignxDeviceAdminReceiver::class.java)

    private fun dpm(ctx: Context): DevicePolicyManager? =
        ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager

    fun isDeviceOwner(ctx: Context): Boolean =
        try { dpm(ctx)?.isDeviceOwnerApp(ctx.packageName) == true } catch (_: Exception) { false }

    fun inLockTask(ctx: Context): Boolean = try {
        val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
        am != null && am.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE
    } catch (_: Exception) {
        false
    }

    /**
     * Device Owner only, when Lock player is off, remotely unlocked or left by PIN: undo everything
     * the lockdown set, so the admin can use and factory reset the tablet. Ignores every back-off.
     */
    fun releaseOwner(ctx: Context, homeMode: Boolean) {
        if (isDeviceOwner(ctx)) LockdownApplier.releaseNow(ctx, homeMode)
    }

    /** Setup screen: give up Device Owner. Returns false when Android refused. */
    @Suppress("DEPRECATION") // clearDeviceOwnerApp is the only way for the owner to leave
    fun removeOwner(activity: Activity, homeMode: Boolean): Boolean {
        val dpm = dpm(activity) ?: return false
        try { activity.stopLockTask() } catch (_: Exception) { }
        LockdownApplier.releaseNow(activity, homeMode, full = true) // directly: never skipped by a back-off
        return try {
            dpm.clearDeviceOwnerApp(activity.packageName)
            FileLog.i(TAG, "device owner removed")
            true
        } catch (e: Exception) {
            FileLog.w(TAG, "device owner removal failed: ${e.message}")
            false
        }
    }

    /**
     * Setup screen, Device Owner only: erases the whole device. DISALLOW_FACTORY_RESET only hides and
     * blocks the reset button in Settings (a restriction on the user); wipeData is the owner's own
     * call and is not subject to it. The lockdown is released first anyway so nothing is left half set.
     * Returns false when Android refused (the device is then unchanged); on success the device reboots.
     */
    fun wipeDevice(activity: Activity, homeMode: Boolean): Boolean {
        if (!FactoryReset.offered(isDeviceOwner(activity))) return false
        val dpm = dpm(activity) ?: return false
        try { activity.stopLockTask() } catch (_: Exception) { }
        LockdownApplier.releaseNow(activity, homeMode, full = true)
        return try {
            FileLog.w(TAG, "factory reset requested from the setup screen")
            dpm.wipeData(0)
            true
        } catch (e: Exception) {
            FileLog.w(TAG, "factory reset refused: ${e.message}")
            false
        }
    }

    /** Enables or disables the Home alias. Throws when the change did not take effect (never swallowed). */
    fun setHomeAlias(ctx: Context, enabled: Boolean) {
        val state = if (enabled) PackageManager.COMPONENT_ENABLED_STATE_ENABLED
        else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
        val name = ComponentName(ctx.packageName, HOME_ALIAS)
        ctx.packageManager.setComponentEnabledSetting(name, state, PackageManager.DONT_KILL_APP)
        check(ctx.packageManager.getComponentEnabledSetting(name) == state) { "home alias not ${if (enabled) "enabled" else "disabled"}" }
    }

    /** For screens that only want to try: logs a failure instead of throwing. */
    fun trySetHomeAlias(ctx: Context, enabled: Boolean): Boolean = try {
        setHomeAlias(ctx, enabled)
        true
    } catch (e: Exception) {
        FileLog.w(TAG, "home alias toggle failed: ${e.message}")
        false
    }
}
