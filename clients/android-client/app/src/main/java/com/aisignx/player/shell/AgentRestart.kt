package com.aisignx.player.shell

import android.app.ActivityManager
import android.app.AlarmManager
import android.app.ApplicationExitInfo
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Process
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog
import com.aisignx.player.ui.LockTask
import com.aisignx.player.ui.PlayerActivity

/**
 * Process-level restart paths, shared by the watchdog, the crash handler and restart_player.
 *
 * What Android permits: an app can always kill its own process and can always recreate an
 * activity that is on screen. It cannot always start an activity from the background: on
 * Android 10+ that is refused unless the app is Device Owner, the Home app, or otherwise
 * exempt. So [restartProcess] schedules the launch first and kills second, but on a plain
 * device the relaunch can be refused, and the screen then stays dark until someone opens the
 * app. Callers prefer recreating the activity and use this only when that is not enough.
 */
object AgentRestart {
    private const val TAG = "Agent"
    private const val RELAUNCH_DELAY_MS = 1_500L

    /** Device Owner or Home mode: the relaunch from the background is allowed (or done by the system). */
    fun relaunchIsReliable(ctx: Context): Boolean =
        LockTask.isDeviceOwner(ctx) || AiSignXApp.from(ctx).config.homeMode

    /** Schedules the player launch (AlarmManager fires it after the process is gone), then kills the process. */
    fun restartProcess(ctx: Context, reason: String) {
        FileLog.w(TAG, "restarting the process: $reason")
        scheduleRelaunch(ctx)
        Process.killProcess(Process.myPid())
    }

    /** Only schedules; the caller lets the process die (crash handler). Never throws. */
    fun scheduleRelaunch(ctx: Context) {
        try {
            val intent = Intent(ctx, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            val pi = PendingIntent.getActivity(ctx, 7301, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            // Inexact on purpose: exact alarms need a permission that Play policy restricts. May be a few seconds late.
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, android.os.SystemClock.elapsedRealtime() + RELAUNCH_DELAY_MS, pi)
        } catch (e: Exception) {
            FileLog.w(TAG, "could not schedule the relaunch: ${e.message}")
        }
    }

    /**
     * Uncaught exception: write the stack to the log, and (watchdog switched on) ask for a relaunch
     * at most once per 10 minutes, then let the previous handler end the process.
     */
    fun installCrashHandler(app: AiSignXApp) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try {
                FileLog.e(TAG, "uncaught exception in thread ${t.name}", e)
                val store = AgentStore(app)
                val now = System.currentTimeMillis()
                if (app.config.isConfigured && store.settings.watchdog &&
                    AgentRates.crashRestartAllowed(store.lastCrashRestartMs, now)
                ) {
                    store.lastCrashRestartMs = now
                    store.addRestart(now)
                    FileLog.w(TAG, "crash: asking for a player relaunch")
                    scheduleRelaunch(app)
                }
            } catch (_: Throwable) {
                // The crash path must never throw.
            }
            previous?.uncaughtException(t, e)
        }
    }

    /** API 30+: why did the last processes end? Logged once at start; guarded, best effort. */
    fun logExitReasons(ctx: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        try {
            val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            for (i in am.getHistoricalProcessExitReasons(ctx.packageName, 0, 3)) {
                FileLog.i(TAG, "previous exit: ${reasonName(i.reason)} status=${i.status} importance=${i.importance} at=${i.timestamp} ${i.description.orEmpty().take(120)}")
            }
        } catch (e: Throwable) {
            FileLog.w(TAG, "exit reasons unavailable: ${e.message}")
        }
    }

    private fun reasonName(r: Int): String = when (r) {
        ApplicationExitInfo.REASON_ANR -> "ANR"
        ApplicationExitInfo.REASON_CRASH -> "CRASH"
        ApplicationExitInfo.REASON_CRASH_NATIVE -> "CRASH_NATIVE"
        ApplicationExitInfo.REASON_EXIT_SELF -> "EXIT_SELF"
        ApplicationExitInfo.REASON_LOW_MEMORY -> "LOW_MEMORY"
        ApplicationExitInfo.REASON_SIGNALED -> "SIGNALED"
        ApplicationExitInfo.REASON_USER_REQUESTED -> "USER_REQUESTED"
        ApplicationExitInfo.REASON_USER_STOPPED -> "USER_STOPPED"
        ApplicationExitInfo.REASON_PERMISSION_CHANGE -> "PERMISSION_CHANGE"
        ApplicationExitInfo.REASON_DEPENDENCY_DIED -> "DEPENDENCY_DIED"
        ApplicationExitInfo.REASON_INITIALIZATION_FAILURE -> "INIT_FAILURE"
        else -> "OTHER($r)"
    }
}
