package com.aisignx.player.ui

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog

/** Device side of [LocalUnlock]: persisted end time, the Setup-in-front flag and the alarm at the end. */
object LocalUnlockWindow {
    private const val TAG = "LocalUnlock"
    private const val REQUEST = 7303

    /** Set by SetupActivity between onResume and onPause: never engage under the Setup screen. */
    @Volatile var setupInFront = false

    fun hold(ctx: Context): Boolean =
        LocalUnlock.hold(AiSignXApp.from(ctx).config.localUnlockUntil, System.currentTimeMillis(), setupInFront)

    fun remainingMs(ctx: Context): Long =
        LocalUnlock.remainingMs(AiSignXApp.from(ctx).config.localUnlockUntil, System.currentTimeMillis())

    /** After a correct PIN exit: open the window and arrange for the lockdown to come back at its end. */
    fun start(ctx: Context) {
        val until = LocalUnlock.endFrom(System.currentTimeMillis())
        AiSignXApp.from(ctx).config.localUnlockUntil = until
        FileLog.i(TAG, "local unlock window open for ${LocalUnlock.WINDOW_MS / 60_000} min")
        schedule(ctx, until)
    }

    /** End it now (button on the Setup screen). The caller starts the player, which engages again. */
    fun end(ctx: Context) {
        AiSignXApp.from(ctx).config.localUnlockUntil = 0L
        cancel(ctx)
        FileLog.i(TAG, "local unlock window ended")
    }

    private fun intent(ctx: Context) = PendingIntent.getBroadcast(
        ctx, REQUEST, Intent(ctx, LocalUnlockEndReceiver::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun schedule(ctx: Context, until: Long) {
        try {
            val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, until + 500L, intent(ctx))
        } catch (e: Exception) {
            FileLog.w(TAG, "could not schedule the end of the window: ${e.message}")
        }
    }

    private fun cancel(ctx: Context) {
        try { (ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager).cancel(intent(ctx)) } catch (_: Exception) { }
    }
}

/** Fires when the local unlock window is over (not exported): the lockdown comes back. */
class LocalUnlockEndReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        FileLog.i("LocalUnlock", "window over")
        try {
            LockdownApplier.syncStored(context) // honours a Setup screen still in front
            if (!LocalUnlockWindow.setupInFront && LockTask.isDeviceOwner(context)) {
                // Bring the player back; Android may refuse a start from the background, which is only logged.
                context.startActivity(Intent(context, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        } catch (e: Exception) {
            FileLog.w("LocalUnlock", "re-engage after the window failed: ${e.message}")
        }
    }
}
