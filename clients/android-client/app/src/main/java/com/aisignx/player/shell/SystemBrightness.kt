package com.aisignx.player.shell

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.os.Build
import android.provider.Settings
import com.aisignx.player.FileLog
import com.aisignx.player.ui.LockTask

/**
 * The device's own brightness setting, for a Device Owner (Android 9+): some devices ignore the window's
 * brightness (seen on a Samsung tablet on Android 16), so the app sets Settings.System as well. The user's own
 * mode and value are kept when the app first dims the screen and put back at 100 %. The rules are in
 * Brightness.systemStep (unit tested); this file only makes the Android calls. Not a Device Owner: nothing.
 */
object SystemBrightness {
    private const val TAG = "Brightness"
    private const val PREFS = "system_brightness"
    private const val MANAGED = "managed"
    private const val OWN_MODE = "own_mode"
    private const val OWN_VALUE = "own_value"
    private const val MANUAL = Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL

    fun apply(ctx: Context, level: Int) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P || !LockTask.isDeviceOwner(ctx)) return
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val step = Brightness.systemStep(level, prefs.getBoolean(MANAGED, false)) ?: return
        try {
            val dpm = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager ?: return
            val admin = LockTask.admin(ctx)
            when (step) {
                is Brightness.SystemStep.Set -> {
                    if (step.saveOwn) {
                        val cr = ctx.contentResolver
                        prefs.edit()
                            .putInt(OWN_MODE, Settings.System.getInt(cr, Settings.System.SCREEN_BRIGHTNESS_MODE, MANUAL))
                            .putInt(OWN_VALUE, Settings.System.getInt(cr, Settings.System.SCREEN_BRIGHTNESS, 255))
                            .putBoolean(MANAGED, true).apply()
                    }
                    dpm.setSystemSetting(admin, Settings.System.SCREEN_BRIGHTNESS_MODE, MANUAL.toString())
                    dpm.setSystemSetting(admin, Settings.System.SCREEN_BRIGHTNESS, step.value.toString())
                    FileLog.i(TAG, "system brightness $level% (${step.value}/255)")
                }
                Brightness.SystemStep.Restore -> {
                    dpm.setSystemSetting(admin, Settings.System.SCREEN_BRIGHTNESS, prefs.getInt(OWN_VALUE, 255).toString())
                    dpm.setSystemSetting(admin, Settings.System.SCREEN_BRIGHTNESS_MODE, prefs.getInt(OWN_MODE, MANUAL).toString())
                    prefs.edit().putBoolean(MANAGED, false).apply()
                    FileLog.i(TAG, "system brightness back to the device's own setting")
                }
            }
        } catch (e: Exception) {
            FileLog.w(TAG, "system brightness: ${e.javaClass.simpleName}: ${e.message}")
        }
    }
}
