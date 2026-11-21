package com.aisignx.player.shell

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import com.aisignx.player.FileLog
import com.aisignx.player.boot.BootReceiver
import java.io.File

/**
 * Installing a verified package with PackageInstaller, and the autostart switch.
 *
 * As Device Owner the install is silent (Android 12+ also gets USER_ACTION_NOT_REQUIRED). On any other
 * device the system shows its install prompt and a person has to confirm it; the app must also be
 * allowed to "install unknown apps" for that. The session is limited to this app's own package
 * name, so Android refuses a package that is not this app (and one signed by another key).
 * No FileProvider is needed: the bytes go into the session, not through a file URI.
 */
object Installer {
    private const val TAG = "Installer"
    const val ACTION_RESULT = "com.aisignx.player.INSTALL_RESULT"
    private const val UPDATE_DIR = "update"
    const val UPDATE_FILE = "update.apk"

    fun updateFile(ctx: Context): File = File(File(ctx.filesDir, UPDATE_DIR), UPDATE_FILE)

    fun cleanUp(ctx: Context) {
        try { File(ctx.filesDir, UPDATE_DIR).deleteRecursively() } catch (_: Exception) { }
    }

    /** Returns null when the session was committed (the result arrives at [InstallResultReceiver]), else why not. */
    fun install(ctx: Context, apk: File): String? {
        return try {
            val pi = ctx.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            params.setAppPackageName(ctx.packageName)
            params.setSize(apk.length())
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val id = pi.createSession(params)
            pi.openSession(id).use { session ->
                session.openWrite("package", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out) }
                    session.fsync(out)
                }
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
                val result = Intent(ctx, InstallResultReceiver::class.java).setAction(ACTION_RESULT)
                session.commit(PendingIntent.getBroadcast(ctx, id, result, flags).intentSender)
            }
            null
        } catch (e: Exception) {
            FileLog.w(TAG, "install failed: ${e.javaClass.simpleName}")
            "install failed"
        }
    }

    // ---- autostart --------------------------------------------------------------------

    private fun bootComponent(ctx: Context) = ComponentName(ctx, BootReceiver::class.java)

    /** The boot receiver is enabled (the manifest default) unless a command switched it off. */
    fun autostart(ctx: Context): Boolean = try {
        when (ctx.packageManager.getComponentEnabledSetting(bootComponent(ctx))) {
            PackageManager.COMPONENT_ENABLED_STATE_DISABLED, PackageManager.COMPONENT_ENABLED_STATE_DISABLED_USER -> false
            else -> true
        }
    } catch (_: Exception) { true }

    fun setAutostart(ctx: Context, on: Boolean) {
        val state = if (on) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
        ctx.packageManager.setComponentEnabledSetting(bootComponent(ctx), state, PackageManager.DONT_KILL_APP)
    }
}

/** The installer's answer (not exported). A person's confirmation, when needed, is started from here. */
class InstallResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Installer.ACTION_RESULT) return
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                try {
                    if (confirm != null) context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    FileLog.i(TAG, "install needs a person to confirm")
                } catch (e: Exception) {
                    FileLog.w(TAG, "could not show the install prompt: ${e.javaClass.simpleName}")
                    val store = AgentStore(context)
                    store.updateError = TierRules.storeUpdateError(store.updateVersion.ifEmpty { "update" }, "needs a person to confirm", System.currentTimeMillis())
                }
            }
            PackageInstaller.STATUS_SUCCESS -> {
                FileLog.i(TAG, "install succeeded")
                Installer.cleanUp(context)
            }
            else -> {
                FileLog.w(TAG, "install failed: status $status ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty().take(120)}")
                val store = AgentStore(context)
                store.updatePendingMs = 0L
                store.updateError = TierRules.storeUpdateError(store.updateVersion.ifEmpty { "update" }, failureWord(status), System.currentTimeMillis())
                Installer.cleanUp(context)
            }
        }
    }

    /** A short fixed word for the status code (never text from the installer). */
    private fun failureWord(status: Int): String = when (status) {
        PackageInstaller.STATUS_FAILURE_CONFLICT -> "conflict (signature or downgrade)"
        PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "incompatible"
        PackageInstaller.STATUS_FAILURE_INVALID -> "invalid package (often a different signing key: reinstall the app once)"
        PackageInstaller.STATUS_FAILURE_STORAGE -> "not enough storage"
        PackageInstaller.STATUS_FAILURE_BLOCKED -> "blocked"
        PackageInstaller.STATUS_FAILURE_ABORTED -> "refused by the user"
        else -> "install failed ($status)"
    }

    companion object { private const val TAG = "Installer" }
}
