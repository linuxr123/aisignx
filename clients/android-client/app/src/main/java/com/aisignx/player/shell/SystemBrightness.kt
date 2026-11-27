package com.aisignx.player.shell

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.os.Build
import android.provider.Settings
import com.aisignx.player.FileLog
import com.aisignx.player.ui.LockTask

/**
 * The device's own brightness setting, for a Device Owner (Android 9+): some devices ignore the window's
 * brightness (seen on a Samsung tablet on Android 16), so the app sets Settings.System as well, in manual mode,
 * at the level from AiSignX (100 % is full brightness, not the device's earlier setting). The value is from
 * Brightness.systemValue (unit tested). Not a Device Owner: nothing.
 */
object SystemBrightness {
    private const val TAG = "Brightness"

    fun apply(ctx: Context, level: Int) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P || !LockTask.isDeviceOwner(ctx)) return
        val value = Brightness.systemValue(level)
        try {
            val dpm = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager ?: return
            val admin = LockTask.admin(ctx)
            dpm.setSystemSetting(admin, Settings.System.SCREEN_BRIGHTNESS_MODE, Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL.toString())
            dpm.setSystemSetting(admin, Settings.System.SCREEN_BRIGHTNESS, value.toString())
            FileLog.i(TAG, "system brightness $level% ($value/255)")
        } catch (e: Exception) {
            FileLog.w(TAG, "system brightness: ${e.javaClass.simpleName}: ${e.message}")
        }
    }
}
