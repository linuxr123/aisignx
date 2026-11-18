package com.aisignx.player.ui

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.provider.Settings
import com.aisignx.player.FileLog

/**
 * Samsung's Settings can close itself when Android thinks set-up is unfinished after QR provisioning.
 * These are the two flags it looks at; they are logged (never sent anywhere) at every app start and
 * when the Setup screen opens, so a field log shows the state.
 */
object SetupFlags {
    private const val TAG = "SetupFlags"

    /** Settings.Secure.USER_SETUP_COMPLETE is hidden API; the key string is the stable public value. */
    const val USER_SETUP_COMPLETE = "user_setup_complete"

    /** Pure formatter: null is a setting that does not exist. */
    fun describe(userSetupComplete: Int?, deviceProvisioned: Int?): String =
        "user_setup_complete=${value(userSetupComplete)} device_provisioned=${value(deviceProvisioned)}"

    private fun value(v: Int?) = v?.toString() ?: "unset"

    fun userSetupComplete(ctx: Context): Int? = try {
        Settings.Secure.getInt(ctx.contentResolver, USER_SETUP_COMPLETE)
    } catch (_: Exception) { null }

    fun deviceProvisioned(ctx: Context): Int? = try {
        Settings.Global.getInt(ctx.contentResolver, Settings.Global.DEVICE_PROVISIONED)
    } catch (_: Exception) { null }

    fun log(ctx: Context, where: String) {
        FileLog.i(TAG, "$where: ${describe(userSetupComplete(ctx), deviceProvisioned(ctx))}")
    }

    /**
     * Device Owner only, and only when the flag is 0: asks Android to mark user set-up complete with
     * the public DevicePolicyManager.setSecureSetting. Whether Android allows this key for a Device
     * Owner depends on the Android version (the allow-list is not documented for this key), so a
     * refusal is logged and ignored. No hidden API, no root. Returns true when the flag reads 1 after.
     */
    fun tryMarkComplete(ctx: Context): Boolean {
        if (userSetupComplete(ctx) != 0 || !LockTask.isDeviceOwner(ctx)) return false
        return try {
            val dpm = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
            dpm.setSecureSetting(LockTask.admin(ctx), USER_SETUP_COMPLETE, "1")
            val ok = userSetupComplete(ctx) == 1
            FileLog.i(TAG, "mark user set-up complete: ${if (ok) "done" else "accepted but not changed"}")
            ok
        } catch (e: Exception) {
            FileLog.w(TAG, "mark user set-up complete refused: ${e.javaClass.simpleName}")
            false
        }
    }
}
