package com.aisignx.player.ui

import android.app.admin.DevicePolicyManager
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.os.Build
import android.os.Process
import android.os.SystemClock
import android.provider.Settings
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog

/**
 * Runs [Lockdown] against the real DevicePolicyManager (Device Owner only). See docs/android-kiosk.md.
 * The decisions (per-mode back-off, never gating a release, the crash-loop breaker) are in
 * [LockdownRunner]; this object supplies the device calls, the stored state and the retry alarm.
 */
object LockdownApplier {
    private const val TAG = "Lockdown"
    private const val PREFS = "lockdown"
    private const val RETRY_MS = 60_000L
    private const val RETRY_REQUEST = 7302

    private val prefsState = object : LockdownRunner.State {
        lateinit var prefs: SharedPreferences
        // stay_on: an engage that saved the old value but pre-dates the flag still counts as engaged.
        override var engaged: Boolean
            get() = prefs.getBoolean("engaged", false) || prefs.contains("stay_on")
            set(v) { prefs.edit().putBoolean("engaged", v).commit() }
        override var releasePending: Boolean
            get() = prefs.getBoolean("release_pending", false)
            set(v) { prefs.edit().putBoolean("release_pending", v).commit() }
        override var tripped: Boolean
            get() = prefs.getBoolean("tripped", false)
            set(v) { prefs.edit().putBoolean("tripped", v).commit() }
    }
    private val runner = LockdownRunner(prefsState) { SystemClock.elapsedRealtime() }

    private fun runner(ctx: Context): LockdownRunner {
        prefsState.prefs = ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return runner
    }

    /** Applies [mode] (see [LockdownRunner.sync]). Any thread. */
    @Synchronized
    fun sync(ctx: Context, mode: Lockdown.Mode, homeMode: Boolean) {
        val r = runner(ctx)
        if (mode == Lockdown.Mode.NONE) return
        val failed = r.sync(DpmOps(ctx.applicationContext), mode, homeMode) ?: return
        report(ctx, r.effective(mode), failed)
    }

    /** Boot, update, provisioning: apply what the last manifest said, without the player running. */
    fun syncStored(ctx: Context) {
        val config = AiSignXApp.from(ctx).config
        sync(ctx, Lockdown.decide(LockTask.isDeviceOwner(ctx), config.kioskLock, false), config.homeMode)
    }

    /** PIN exit or removal of the Device Owner: release now, ignoring every gate. Safe on a non-owner. */
    @Synchronized
    fun releaseNow(ctx: Context, homeMode: Boolean, full: Boolean = false) {
        if (!LockTask.isDeviceOwner(ctx)) return
        report(ctx, Lockdown.Mode.RELEASE, runner(ctx).releaseNow(DpmOps(ctx.applicationContext), homeMode, full))
    }

    /** Process start (Application.onCreate): count it for the crash-loop breaker, finish a pending release. */
    @Synchronized
    fun onProcessStart(ctx: Context) {
        val r = runner(ctx)
        val prefs = prefsState.prefs
        val now = System.currentTimeMillis()
        val starts = CrashLoop.record(CrashLoop.parse(prefs.getString("starts", null)), now)
        prefs.edit().putString("starts", CrashLoop.format(starts)).commit()
        if (!LockTask.isDeviceOwner(ctx)) return
        if (CrashLoop.tripped(starts, now) && !r.tripped) {
            FileLog.w(TAG, "the player started ${starts.size} times in ${CrashLoop.WINDOW_MS / 60_000} minutes without a manifest: releasing the lockdown")
            r.trip()
            // The break must not wait for a player that may crash first.
            report(ctx, Lockdown.Mode.RELEASE, r.releaseNow(DpmOps(ctx.applicationContext), AiSignXApp.from(ctx).config.homeMode, true))
        } else if (r.releasePending) {
            retryRelease(ctx)
        }
    }

    /** A manifest came from the server (any thread): the app is healthy; a tripped breaker is lifted. */
    @Synchronized
    fun onManifestFetched(ctx: Context) {
        val r = runner(ctx)
        val uptime = SystemClock.elapsedRealtime() - Process.getStartElapsedRealtime()
        if (CrashLoop.healthy(uptime) && prefsState.prefs.contains("starts")) {
            prefsState.prefs.edit().remove("starts").commit()
        }
        if (r.tripped) {
            FileLog.i(TAG, "a manifest was fetched: the crash-loop breaker is off")
            r.manifestFetched() // the player's next lock check engages again when Lock player is on
        }
    }

    /** The retry alarm (or a process start) found a release still pending. */
    @Synchronized
    fun retryRelease(ctx: Context) {
        val r = runner(ctx)
        if (!LockTask.isDeviceOwner(ctx)) return
        val failed = r.retryPendingRelease(DpmOps(ctx.applicationContext), AiSignXApp.from(ctx).config.homeMode) ?: return
        report(ctx, Lockdown.Mode.RELEASE, failed)
    }

    private fun report(ctx: Context, mode: Lockdown.Mode, failed: List<String>) {
        if (failed.isEmpty()) {
            FileLog.i(TAG, "lockdown ${mode.name.lowercase()}: done")
            if (mode == Lockdown.Mode.RELEASE && !prefsState.releasePending) cancelRetry(ctx)
        } else {
            FileLog.w(TAG, "lockdown ${mode.name.lowercase()}: failed steps $failed")
            if (mode == Lockdown.Mode.RELEASE) scheduleRetry(ctx) // does not depend on Lock player being on
        }
    }

    private fun retryIntent(ctx: Context) = PendingIntent.getBroadcast(
        ctx, RETRY_REQUEST, Intent(ctx, LockdownRetryReceiver::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun scheduleRetry(ctx: Context) {
        try {
            val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + RETRY_MS, retryIntent(ctx))
        } catch (e: Exception) {
            FileLog.w(TAG, "could not schedule the release retry: ${e.message}")
        }
    }

    private fun cancelRetry(ctx: Context) {
        try { (ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager).cancel(retryIntent(ctx)) } catch (_: Exception) { }
    }

    private class DpmOps(private val ctx: Context) : Lockdown.Ops {
        private val dpm = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
        private val admin = LockTask.admin(ctx)
        private val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        override val packageName: String get() = ctx.packageName

        override var stayOnBackup: Int?
            get() = if (prefs.contains("stay_on")) prefs.getInt("stay_on", 0) else null
            set(v) { prefs.edit().apply { if (v == null) remove("stay_on") else putInt("stay_on", v) }.commit() }

        override fun currentStayOn(): Int? = try {
            Settings.Global.getInt(ctx.contentResolver, Settings.Global.STAY_ON_WHILE_PLUGGED_IN)
        } catch (_: Settings.SettingNotFoundException) { null }

        override fun setLockTaskPackages(packages: List<String>) = dpm.setLockTaskPackages(admin, packages.toTypedArray())

        override fun setLockTaskFeatures(features: Int) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) dpm.setLockTaskFeatures(admin, features)
        }

        override fun setKeyguardDisabled(disabled: Boolean) {
            // False means "not possible" (a secure lock screen is set); not an error worth retrying.
            if (!dpm.setKeyguardDisabled(admin, disabled) && disabled) FileLog.w(TAG, "keyguard could not be disabled")
        }

        override fun setStatusBarDisabled(disabled: Boolean) {
            if (!dpm.setStatusBarDisabled(admin, disabled) && disabled) FileLog.w(TAG, "status bar could not be disabled")
        }

        override fun setStayOn(value: Int) = dpm.setGlobalSetting(admin, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, value.toString())
        override fun addRestriction(name: String) = dpm.addUserRestriction(admin, name)
        override fun clearRestriction(name: String) = dpm.clearUserRestriction(admin, name)
        override fun setUninstallBlocked(blocked: Boolean) { dpm.setUninstallBlocked(admin, ctx.packageName, blocked) }
        override fun setHomeAlias(enabled: Boolean) = LockTask.setHomeAlias(ctx, enabled)

        override fun setPersistentHome(on: Boolean) {
            if (on) {
                val filter = IntentFilter(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    addCategory(Intent.CATEGORY_DEFAULT)
                }
                dpm.addPersistentPreferredActivity(admin, filter, ComponentName(ctx.packageName, LockTask.HOME_ALIAS))
            } else {
                dpm.clearPackagePersistentPreferredActivities(admin, ctx.packageName)
            }
        }
    }
}

/** Fires after a failed release (not exported): tries again, whatever Lock player says. */
class LockdownRetryReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        LockdownApplier.retryRelease(context)
    }
}
