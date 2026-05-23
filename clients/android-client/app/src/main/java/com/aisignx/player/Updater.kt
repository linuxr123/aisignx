package com.aisignx.player

import android.app.PendingIntent
import android.app.admin.DevicePolicyManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Downloads the latest APK from the server and installs it.
 *
 * Two install paths:
 *   1. SILENT — when the app is the Device Owner (one-time ADB setup), the
 *      PackageInstaller session is approved automatically. No user interaction.
 *      This is the production path for kiosks.
 *   2. INTERACTIVE — when not Device Owner, Android shows the standard
 *      "Install update?" prompt and the user must tap Install. Used for dev
 *      builds and consumer-grade Android devices.
 *
 * After install, the new app auto-launches via UpdateInstallReceiver.
 */
object Updater {

	private const val TAG = "AISignX/Updater"
	private const val SESSION_NAME = "aisignx-update"
	private const val APK_FILENAME = "aisignx-update.apk"

	private val downloadHttp = OkHttpClient.Builder()
		.connectTimeout(20, TimeUnit.SECONDS)
		.readTimeout(120, TimeUnit.SECONDS)
		.hostnameVerifier { _, _ -> true }
		.build()

	/**
	 * Fetch /api/version, find the android entry, download the APK, install it.
	 * Returns true on successful spawn of the install (the new app will take
	 * over from there); false if anything failed before the install was queued.
	 */
	fun runUpdate(ctx: Context): Boolean {
		return try {
			val serverUrl = Config.serverUrl
			if (serverUrl.isEmpty()) {
				FileLog.w(TAG, "no server URL configured")
				return false
			}

			// 1. Get manifest
			val manifest = fetchManifest(serverUrl) ?: return false
			val android = manifest.optJSONObject("clients")?.optJSONObject("android")
				?: run { FileLog.w(TAG, "no android entry in manifest"); return false }
			val urlPath = android.optString("url").takeIf { it.isNotEmpty() }
				?: run { FileLog.w(TAG, "no url for android"); return false }
			val downloadUrl = if (urlPath.startsWith("http://") || urlPath.startsWith("https://")) {
				urlPath
			} else {
				serverUrl.trimEnd('/') + "/" + urlPath.trimStart('/')
			}
			val remoteVer = android.optString("version", "?")
			val localVer = try {
				ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "0.0.0"
			} catch (_: Throwable) { "0.0.0" }
			if (!ApiClient.isRemoteVersionNewer(localVer, remoteVer)) {
				FileLog.i(TAG, "no newer android build ($localVer >= $remoteVer)")
				return false
			}

			FileLog.i(TAG, "downloading $remoteVer from $downloadUrl")

			// 2. Download to private cache
			val apk = File(ctx.cacheDir, APK_FILENAME).also { if (it.exists()) it.delete() }
			if (!downloadTo(downloadUrl, apk)) return false
			FileLog.i(TAG, "download complete: ${apk.length()} bytes")

			// 3. Install
			installApk(ctx, apk)
			true
		} catch (e: Exception) {
			FileLog.e(TAG, "update failed", e)
			false
		}
	}

	private fun fetchManifest(serverUrl: String): JSONObject? {
		val req = Request.Builder().url("$serverUrl/api/version").get().build()
		return try {
			downloadHttp.newCall(req).execute().use { resp ->
				if (!resp.isSuccessful) return null
				JSONObject(resp.body?.string() ?: return null)
			}
		} catch (e: Exception) {
			FileLog.w(TAG, "fetchManifest failed: ${e.message}")
			null
		}
	}

	private fun downloadTo(url: String, file: File): Boolean {
		val req = Request.Builder().url(url).get().build()
		return try {
			downloadHttp.newCall(req).execute().use { resp ->
				if (!resp.isSuccessful) {
					FileLog.w(TAG, "download HTTP ${resp.code}")
					return false
				}
				val body = resp.body ?: return false
				file.outputStream().use { out -> body.byteStream().copyTo(out) }
				true
			}
		} catch (e: IOException) {
			FileLog.w(TAG, "download IO error: ${e.message}")
			false
		}
	}

	/**
	 * Streams the APK into a PackageInstaller session and commits.
	 * On Device Owner devices the session auto-approves with no UI.
	 * Otherwise Android shows the standard install prompt.
	 */
	private fun installApk(ctx: Context, apk: File) {
		val deviceOwner = isDeviceOwner(ctx)
		if (deviceOwner) {
			FileLog.i(TAG, "device owner detected; requesting unattended install")
		} else {
			FileLog.w(TAG, "not device owner; Android may require install approval")
		}
		val installer = ctx.packageManager.packageInstaller
		val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
		params.setAppPackageName(ctx.packageName)
		if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
			params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
		}
		val sessionId = installer.createSession(params)
		FileLog.i(TAG, "install session id=$sessionId")

		installer.openSession(sessionId).use { session ->
			apk.inputStream().use { input ->
				session.openWrite(SESSION_NAME, 0, apk.length()).use { out ->
					input.copyTo(out)
					session.fsync(out)
				}
			}
			// Build a callback intent so we know when the install succeeded /
			// needs user approval / failed.
			val cbIntent = Intent(ctx, UpdateInstallReceiver::class.java).apply {
				action = UpdateInstallReceiver.ACTION_INSTALL_RESULT
			}
			val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M)
				PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
			else PendingIntent.FLAG_UPDATE_CURRENT
			val pi = PendingIntent.getBroadcast(ctx, sessionId, cbIntent, flags)
			session.commit(pi.intentSender)
			FileLog.i(TAG, "install committed; awaiting result via UpdateInstallReceiver")
		}
	}

	private fun isDeviceOwner(ctx: Context): Boolean {
		return try {
			val dpm = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
			dpm.isDeviceOwnerApp(ctx.packageName)
		} catch (_: Throwable) {
			false
		}
	}
}
