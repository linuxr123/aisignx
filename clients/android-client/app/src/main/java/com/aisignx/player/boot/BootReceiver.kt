package com.aisignx.player.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog
import com.aisignx.player.shell.AgentStore
import com.aisignx.player.shell.TierRules
import com.aisignx.player.ui.LockdownApplier
import com.aisignx.player.ui.PlayerActivity

/**
 * Starts the player after a reboot when a server is configured. This receiver is the "autostart"
 * switch of the set_startup command: disabled, the player no longer starts from here (Home mode and
 * the Device Owner lockdown still make it the Home app).
 *
 * Android 10+ blocks activity starts from the background, so this start only
 * succeeds on some devices (older Android, some TV boxes, Device Owner). The reliable way to
 * start at boot is the "Use as Home app" option in setup: the launcher then is
 * the player. This receiver is the fallback.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val app = AiSignXApp.from(context)
        // A reboot ends the on-device unlock window: the tablet always comes back locked (if Lock player is on) and
        // goes straight to the player, however long was left on the window.
        com.aisignx.player.ui.LocalUnlockWindow.end(context)
        // Device Owner: the lockdown from the last manifest is back before the player is.
        LockdownApplier.syncStored(context)
        if (!TierRules.startAtBoot(app.config.isConfigured, autostart = true)) return
        startPlayer(context, "boot")
    }

    companion object {
        private const val TAG = "Boot"

        fun startPlayer(context: Context, why: String) {
            try {
                context.startActivity(Intent(context, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                FileLog.i(TAG, "$why: player start requested")
            } catch (e: Exception) {
                FileLog.w(TAG, "$why: player start refused: ${e.message}")
            }
        }
    }
}

/**
 * The app was replaced (an update). After an update the app is stopped; it is restarted only where
 * that is the intended kiosk (Home mode, Lock player) or when an update_app command asked for the update.
 * A separate receiver, so switching autostart off does not stop this.
 */
class PackageReplacedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val app = AiSignXApp.from(context)
        val store = AgentStore(context)
        val pending = store.updatePendingMs > 0L
        store.updatePendingMs = 0L
        LockdownApplier.syncStored(context)
        val c = app.config
        if (TierRules.startAfterUpdate(c.isConfigured, c.homeMode, c.kioskLock, pending)) BootReceiver.startPlayer(context, "update")
    }
}
