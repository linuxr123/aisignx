package com.aisignx.player

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileWriter
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Append-only file logger that mirrors what the Electron client does on
 * Windows. Lets us diagnose update / SSE / install failures on the device
 * without needing ADB. The log lives at:
 *   /sdcard/Android/data/com.aisignx.player/files/aisignx-player.log
 *
 * Rotates at 1 MB by archiving to .1 (single-generation rotation).
 *
 * Use like:
 *   FileLog.init(context)
 *   FileLog.i("Player", "started")
 *   FileLog.e("Update", "failed", throwable)
 */
object FileLog {

	private const val MAX_BYTES = 1_048_576L          // 1 MB
	private const val FILENAME  = "aisignx-player.log"
	private val tsFmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US)

	private var logFile: File? = null
	private var logFileOld: File? = null

	fun init(ctx: Context) {
		val dir = ctx.getExternalFilesDir(null) ?: ctx.filesDir
		logFile = File(dir, FILENAME)
		logFileOld = File(dir, "$FILENAME.1")
		write("=== AISignX Player log opened (v${appVersion(ctx)}) ===")
	}

	fun i(tag: String, msg: String) { Log.i(tag, msg); write("[I/$tag] $msg") }
	fun w(tag: String, msg: String) { Log.w(tag, msg); write("[W/$tag] $msg") }
	fun e(tag: String, msg: String, t: Throwable? = null) {
		Log.e(tag, msg, t)
		val sw = StringWriter()
		t?.printStackTrace(PrintWriter(sw))
		write("[E/$tag] $msg" + (if (t != null) "\n$sw" else ""))
	}

	@Synchronized
	private fun write(line: String) {
		val f = logFile ?: return
		try {
			// Rotate if oversized
			if (f.exists() && f.length() > MAX_BYTES) {
				logFileOld?.delete()
				f.renameTo(logFileOld ?: return)
			}
			FileWriter(f, true).use { it.append("[${tsFmt.format(Date())}] $line\n") }
		} catch (_: Exception) {
			// Disk full / permission revoked — silently drop
		}
	}

	private fun appVersion(ctx: Context): String =
		try { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?" }
		catch (_: Exception) { "?" }

	/** Absolute path the user can find on the device for emailing/inspecting. */
	fun logPath(): String = logFile?.absolutePath ?: "(uninitialised)"
}
