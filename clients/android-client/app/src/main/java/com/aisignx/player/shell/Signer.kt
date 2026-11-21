package com.aisignx.player.shell

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import java.security.MessageDigest

/**
 * The SHA-256 of the key an app is signed with. Android installs an update only when the new APK is signed with the same
 * key as the installed app, so the app reports its own digest (the server refuses to push an installer with another one),
 * and checks a downloaded installer before it says "installing".
 */
object Signer {
    fun hex(der: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    @Suppress("DEPRECATION")
    private fun digestOf(info: PackageInfo?): String? {
        if (info == null) return null
        val sigs = if (Build.VERSION.SDK_INT >= 28) {
            val si = info.signingInfo ?: return null
            if (si.hasMultipleSigners()) si.apkContentsSigners else si.signingCertificateHistory
        } else info.signatures
        // With key rotation the history lists old keys first; the current key is the last one.
        return sigs?.lastOrNull()?.toByteArray()?.let { hex(it) }
    }

    /** This app's own signing key, or null when it cannot be read. */
    @Suppress("DEPRECATION")
    fun own(ctx: Context): String? = try {
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        digestOf(ctx.packageManager.getPackageInfo(ctx.packageName, flags))
    } catch (_: Exception) { null }

    /** The signing key of an APK file, or null when it cannot be read (then the install is simply tried). */
    @Suppress("DEPRECATION")
    fun ofArchive(ctx: Context, path: String): String? = try {
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        digestOf(ctx.packageManager.getPackageArchiveInfo(path, flags))
    } catch (_: Exception) { null }

    /** A sentence for the admin when the two keys differ (both known), else null. Pure, unit tested. */
    fun mismatch(own: String?, archive: String?): String? =
        if (own != null && archive != null && !own.equals(archive, ignoreCase = true))
            "the installer is signed with a different key than this app, so Android would refuse the update: reinstall the app once from the Downloads page"
        else null
}
