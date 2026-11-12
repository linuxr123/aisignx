package com.aisignx.player.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog
import com.aisignx.player.ui.PlayerActivity

/**
 * Starts the player after a reboot when a server is configured.
 *
 * Android 10+ blocks activity starts from the background, so this start only
 * succeeds on some devices (older Android, some TV boxes). The reliable way to
 * start at boot is the "Use as Home app" option in setup: the launcher then is
 * the player. This receiver is the fallback.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val app = AiSignXApp.from(context)
        if (!app.config.isConfigured) return
        try {
            context.startActivity(
                Intent(context, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
            FileLog.i(TAG, "boot: player start requested")
        } catch (e: Exception) {
            FileLog.w(TAG, "boot: player start refused: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "Boot"
    }
}
