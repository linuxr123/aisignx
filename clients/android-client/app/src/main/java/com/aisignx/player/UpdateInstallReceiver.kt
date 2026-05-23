package com.aisignx.player

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.aisignx.player.ui.PlayerActivity

/**
 * Receives the PackageInstaller commit callback.
 *
 * Three possible outcomes:
 *   STATUS_PENDING_USER_ACTION → show the system install prompt (non-DeviceOwner devices)
 *   STATUS_SUCCESS             → relaunch the app
 *   anything else              → log and post a notification so the failure is visible
 */
class UpdateInstallReceiver : BroadcastReceiver() {

	companion object {
		const val ACTION_INSTALL_RESULT = "com.aisignx.player.INSTALL_RESULT"
		private const val TAG = "AISignX/InstallRcvr"
		private const val CHANNEL_ID = "aisignx_update"
	}

	override fun onReceive(context: Context, intent: Intent) {
		val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -999)
		val msg    = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: ""
		Log.i(TAG, "install status=$status msg=$msg")

		when (status) {
			PackageInstaller.STATUS_PENDING_USER_ACTION -> {
				// Not Device Owner — Android needs the user to approve the install.
				@Suppress("DEPRECATION")
				val confirmIntent = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
				if (confirmIntent != null) {
					confirmIntent.flags = Intent.FLAG_ACTIVITY_NEW_TASK
					try { context.startActivity(confirmIntent) }
					catch (e: Exception) {
						Log.e(TAG, "failed to show install prompt", e)
						notify(context, "Update needs approval",
							"Tap to install AISignX Player update.")
					}
				}
			}
			PackageInstaller.STATUS_SUCCESS -> {
				FileLog.i(TAG, "install succeeded - relaunching player")
				val launch = Intent(context, PlayerActivity::class.java).apply {
					flags = Intent.FLAG_ACTIVITY_NEW_TASK or
							Intent.FLAG_ACTIVITY_CLEAR_TASK
				}
				try { context.startActivity(launch) }
				catch (e: Exception) { FileLog.e(TAG, "relaunch failed", e) }
			}
			else -> {
				FileLog.w(TAG, "install failed: status=$status msg=$msg")
				notify(context, "Update failed", "Status $status: $msg")
			}
		}
	}

	private fun notify(ctx: Context, title: String, text: String) {
		try {
			val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
			if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
				val ch = NotificationChannel(CHANNEL_ID, "AISignX Updates",
					NotificationManager.IMPORTANCE_DEFAULT)
				nm.createNotificationChannel(ch)
			}
			val n = NotificationCompat.Builder(ctx, CHANNEL_ID)
				.setSmallIcon(android.R.drawable.stat_sys_download_done)
				.setContentTitle(title)
				.setContentText(text)
				.setAutoCancel(true)
				.build()
			nm.notify(91211, n)
		} catch (e: Exception) {
			Log.e(TAG, "notify failed", e)
		}
	}
}
