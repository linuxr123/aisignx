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
 * Small rotating log in getExternalFilesDir("logs")/aisignx-player.log, readable over
 * adb or a file manager without root. Rotates at 1 MB into a single ".1" file.
 *
 * Never log cookies, Cookie or Set-Cookie headers, or request bodies. Callers log
 * URL paths and status codes only.
 */
object FileLog {

    private const val MAX_BYTES = 1_048_576L
    private const val FILENAME = "aisignx-player.log"

    private val tsFmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US)
    @Volatile private var logFile: File? = null

    fun init(ctx: Context, version: String) {
        val dir = ctx.getExternalFilesDir("logs") ?: File(ctx.filesDir, "logs")
        dir.mkdirs()
        logFile = File(dir, FILENAME)
        write("I", "App", "=== AiSignX Player $version started ===")
    }

    fun i(tag: String, msg: String) { Log.i(TAG_PREFIX + tag, msg); write("I", tag, msg) }

    fun w(tag: String, msg: String) { Log.w(TAG_PREFIX + tag, msg); write("W", tag, msg) }

    fun e(tag: String, msg: String, t: Throwable? = null) {
        Log.e(TAG_PREFIX + tag, msg, t)
        val trace = if (t != null) {
            val sw = StringWriter()
            t.printStackTrace(PrintWriter(sw))
            "\n" + sw
        } else ""
        write("E", tag, msg + trace)
    }

    fun path(): String = logFile?.absolutePath ?: "(not started)"

    @Synchronized
    private fun write(level: String, tag: String, msg: String) {
        val f = logFile ?: return
        try {
            if (f.exists() && f.length() > MAX_BYTES) {
                val old = File(f.parentFile, "$FILENAME.1")
                old.delete()
                f.renameTo(old)
            }
            FileWriter(f, true).use { it.append("[${tsFmt.format(Date())}] $level/$tag $msg\n") }
        } catch (_: Exception) {
            // Disk full or storage gone: logging must never break playback.
        }
    }

    private const val TAG_PREFIX = "AiSignX/"
}
