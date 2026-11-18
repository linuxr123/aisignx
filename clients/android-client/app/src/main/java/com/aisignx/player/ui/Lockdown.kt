package com.aisignx.player.ui

import android.app.admin.DevicePolicyManager
import android.os.UserManager

/**
 * What the Device Owner lockdown does and when, without Android calls (JVM unit tests).
 * [LockdownApplier] runs it against the real DevicePolicyManager.
 *
 * Rule of the whole design: whatever [engage] sets, [release] undoes, and [release] can always be
 * run (it is idempotent and never stops at a failing step), so the admin can turn the lockdown
 * off from the web page (Lock player off) and factory reset the tablet. [LockdownRunner] adds
 * the bookkeeping (never gate a release, retry a failed one, the crash-loop breaker).
 */
object Lockdown {

    enum class Mode { NONE, ENGAGE, RELEASE }

    /**
     * Not Device Owner: nothing to do (screen pinning is the old LockTask behaviour).
     * Lock player on and not remotely unlocked: lock everything down. Anything else: release.
     * A remote unlock lifts it for its time; [localHold] (the local unlock window after a PIN exit, or the
     * Setup screen being in front, see [LocalUnlock]) keeps it released the same way.
     */
    fun decide(owner: Boolean, kioskLock: Boolean, remoteUnlocked: Boolean, localHold: Boolean = false): Mode = when {
        !owner -> Mode.NONE
        kioskLock && !remoteUnlocked && !localHold -> Mode.ENGAGE
        else -> Mode.RELEASE
    }

    /**
     * The user restrictions the lockdown sets: the minimal set that closes the usual ways out of a
     * kiosk. Safe boot would start the tablet without this app; a second user would have its own
     * launcher; the factory reset button in Settings is the one a passer-by must not press (the
     * admin's way is to turn Lock player off first); USB file transfer exposes the files.
     * Not set on purpose: DISALLOW_DEBUGGING_FEATURES, Wi-Fi and volume (an admin may need them;
     * the status bar and Settings are unreachable anyway). USB debugging is NOT switched on: on a
     * QR-provisioned tablet Developer options are off and unreachable, and an adb prompt on a public
     * kiosk is a risk. The way out is the web page, the PIN, and the crash-loop breaker ([CrashLoop]).
     */
    val RESTRICTIONS: List<String> = listOf(
        UserManager.DISALLOW_SAFE_BOOT,
        UserManager.DISALLOW_ADD_USER,
        UserManager.DISALLOW_FACTORY_RESET,
        UserManager.DISALLOW_USB_FILE_TRANSFER,
    )

    /** Settings.Global.STAY_ON_WHILE_PLUGGED_IN bit flags: AC (1) + USB (2) + wireless (4). */
    const val STAY_ON_ALWAYS = 7

    /** Lock task features kept: the power menu only. HOME, OVERVIEW, NOTIFICATIONS, SYSTEM_INFO and KEYGUARD stay off. */
    const val LOCK_TASK_FEATURES = DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS

    /** Every device policy call the lockdown makes. A step that throws must not stop the others. */
    interface Ops {
        val packageName: String
        /** The STAY_ON_WHILE_PLUGGED_IN value from before the lockdown, or null when none was saved. */
        var stayOnBackup: Int?
        fun currentStayOn(): Int?
        fun setLockTaskPackages(packages: List<String>)
        fun setLockTaskFeatures(features: Int)
        fun setKeyguardDisabled(disabled: Boolean)
        fun setStatusBarDisabled(disabled: Boolean)
        fun setStayOn(value: Int)
        fun addRestriction(name: String)
        fun clearRestriction(name: String)
        fun setUninstallBlocked(blocked: Boolean)
        fun setPersistentHome(on: Boolean)
        fun setHomeAlias(enabled: Boolean)
    }

    /** Runs every step, collecting the names of the ones that failed. */
    private fun steps(failed: MutableList<String>, vararg s: Pair<String, () -> Unit>) {
        for ((name, run) in s) try { run() } catch (_: Throwable) { failed += name }
    }

    /** Applies the lockdown. Safe to repeat. Returns the failed steps (empty: all done). */
    fun engage(ops: Ops): List<String> {
        val failed = mutableListOf<String>()
        steps(
            failed,
            "lock_task_packages" to { ops.setLockTaskPackages(listOf(ops.packageName)) },
            "lock_task_features" to { ops.setLockTaskFeatures(LOCK_TASK_FEATURES) },
            "keyguard" to { ops.setKeyguardDisabled(true) },
            "status_bar" to { ops.setStatusBarDisabled(true) },
            "stay_on" to {
                if (ops.stayOnBackup == null) ops.stayOnBackup = ops.currentStayOn() ?: 0
                ops.setStayOn(STAY_ON_ALWAYS)
            },
            "uninstall_blocked" to { ops.setUninstallBlocked(true) },
            "home_alias" to { ops.setHomeAlias(true) },
        )
        // The persistent Home activity points at the alias: only set it when the alias is enabled.
        if ("home_alias" in failed) failed += "persistent_home"
        else steps(failed, "persistent_home" to { ops.setPersistentHome(true) })
        for (r in RESTRICTIONS) steps(failed, "restriction:$r" to { ops.addRestriction(r) })
        return failed
    }

    /**
     * Undoes everything [engage] did. [homeMode]: the user's own "Use as Home app" choice, which
     * decides whether the Home alias stays enabled. Safe to repeat, also when nothing was engaged.
     */
    fun release(ops: Ops, homeMode: Boolean): List<String> {
        val failed = mutableListOf<String>()
        for (r in RESTRICTIONS) steps(failed, "restriction:$r" to { ops.clearRestriction(r) })
        steps(
            failed,
            "persistent_home" to { ops.setPersistentHome(false) },
            "uninstall_blocked" to { ops.setUninstallBlocked(false) },
            "status_bar" to { ops.setStatusBarDisabled(false) },
            "keyguard" to { ops.setKeyguardDisabled(false) },
            "stay_on" to { ops.stayOnBackup?.let { ops.setStayOn(it); ops.stayOnBackup = null } },
            "lock_task_packages" to { ops.setLockTaskPackages(emptyList()) },
            "home_alias" to { ops.setHomeAlias(homeMode) },
        )
        return failed
    }

    /**
     * Release for a device where the lockdown was never engaged (first provisioning, every boot):
     * only the idempotent Home alias, so policy the admin set another way is left alone.
     */
    fun releaseLight(ops: Ops, homeMode: Boolean): List<String> {
        val failed = mutableListOf<String>()
        steps(failed, "home_alias" to { ops.setHomeAlias(homeMode) })
        return failed
    }

    /** After a failed run, wait this long before trying again (the watchdog calls every few seconds). */
    const val RETRY_AFTER_FAIL_MS = 60_000L

    fun retryAllowed(lastFailMs: Long, nowMs: Long): Boolean =
        lastFailMs <= 0L || nowMs - lastFailMs >= RETRY_AFTER_FAIL_MS || lastFailMs > nowMs
}
