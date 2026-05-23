package com.aisignx.player

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.aisignx.player.ui.PlayerActivity
import com.aisignx.player.ui.SetupActivity

/**
 * Starts the app automatically after the device boots.
 * Routes to PlayerActivity if already configured, otherwise SetupActivity.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != "android.intent.action.LOCKED_BOOT_COMPLETED") return

        val target = if (Config.isConfigured) PlayerActivity::class.java else SetupActivity::class.java
        val launch = Intent(context, target).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(launch)
    }
}
