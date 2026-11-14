package com.aisignx.player.ui

import android.app.Activity
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
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

    /** Device Owner only: allow-list this app, keep the power menu, become the persistent Home. */
    fun prepareOwner(activity: Activity) {
        val dpm = dpm(activity) ?: return
        val admin = admin(activity)
        try {
            dpm.setLockTaskPackages(admin, arrayOf(activity.packageName))
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                dpm.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS)
            }
            setHomeAlias(activity, true)
            dpm.addPersistentPreferredActivity(admin, homeFilter(), ComponentName(activity.packageName, HOME_ALIAS))
        } catch (e: Exception) {
            FileLog.w(TAG, "device owner setup failed: ${e.message}")
        }
    }

    /**
     * Device Owner only, when Lock player is off: drop the allow-list (PlayerActivity's
     * lockTaskMode="if_whitelisted" would otherwise lock it on every start) and the
     * persistent Home. The Home alias goes back to the setup screen's "Use as Home app".
     */
    fun releaseOwner(activity: Activity, homeMode: Boolean) {
        val dpm = dpm(activity) ?: return
        val admin = admin(activity)
        try {
            dpm.setLockTaskPackages(admin, emptyArray())
            dpm.clearPackagePersistentPreferredActivities(admin, activity.packageName)
            setHomeAlias(activity, homeMode)
        } catch (e: Exception) {
            FileLog.w(TAG, "device owner release failed: ${e.message}")
        }
    }

    /** Setup screen: give up Device Owner. Returns false when Android refused. */
    @Suppress("DEPRECATION") // clearDeviceOwnerApp is the only way for the owner to leave
    fun removeOwner(activity: Activity, homeMode: Boolean): Boolean {
        val dpm = dpm(activity) ?: return false
        try { activity.stopLockTask() } catch (_: Exception) { }
        releaseOwner(activity, homeMode)
        return try {
            dpm.clearDeviceOwnerApp(activity.packageName)
            FileLog.i(TAG, "device owner removed")
            true
        } catch (e: Exception) {
            FileLog.w(TAG, "device owner removal failed: ${e.message}")
            false
        }
    }

    fun setHomeAlias(ctx: Context, enabled: Boolean) {
        val state = if (enabled) PackageManager.COMPONENT_ENABLED_STATE_ENABLED
        else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
        try {
            ctx.packageManager.setComponentEnabledSetting(
                ComponentName(ctx.packageName, HOME_ALIAS), state, PackageManager.DONT_KILL_APP,
            )
        } catch (e: Exception) {
            FileLog.w(TAG, "home alias toggle failed: ${e.message}")
        }
    }

    private fun homeFilter() = IntentFilter(Intent.ACTION_MAIN).apply {
        addCategory(Intent.CATEGORY_HOME)
        addCategory(Intent.CATEGORY_DEFAULT)
    }
}
