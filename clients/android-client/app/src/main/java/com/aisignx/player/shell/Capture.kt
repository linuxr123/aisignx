package com.aisignx.player.shell

import android.app.Activity
import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.view.PixelCopy
import com.aisignx.player.FileLog
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executor

/**
 * Screenshot of the activity window with PixelCopy, which includes the hardware video
 * surface (View.draw would leave video black). The JPEG is scaled so its longest side
 * is at most [MAX_SIDE] and handed back as Base64, or null on any failure.
 */
object Capture {
    private const val TAG = "Capture"
    const val MAX_SIDE = 1920
    private const val QUALITY = 80

    /** Call on the main thread. [done] runs on the main thread. Encoding runs on [worker]. */
    fun capture(activity: Activity, worker: Executor, done: (String?) -> Unit) {
        val main = Handler(Looper.getMainLooper())
        val decor = activity.window?.decorView
        val w = decor?.width ?: 0
        val h = decor?.height ?: 0
        if (w <= 0 || h <= 0) return done(null)
        val bitmap = try {
            Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        } catch (e: Throwable) { // OutOfMemoryError included
            FileLog.w(TAG, "bitmap failed: ${e.message}")
            return done(null)
        }
        try {
            PixelCopy.request(activity.window, bitmap, { result ->
                if (result != PixelCopy.SUCCESS) {
                    FileLog.w(TAG, "PixelCopy failed: $result")
                    bitmap.recycle()
                    done(null)
                    return@request
                }
                try {
                    worker.execute {
                        val b64 = encode(bitmap)
                        main.post { done(b64) }
                    }
                } catch (e: Exception) { // executor shut down
                    bitmap.recycle()
                    done(null)
                }
            }, main)
        } catch (e: Exception) {
            FileLog.w(TAG, "PixelCopy request failed: ${e.message}")
            bitmap.recycle()
            done(null)
        }
    }

    private fun encode(src: Bitmap): String? = try {
        val (w, h) = scaled(src.width, src.height)
        val out = if (w == src.width && h == src.height) src else Bitmap.createScaledBitmap(src, w, h, true)
        val bytes = ByteArrayOutputStream()
        out.compress(Bitmap.CompressFormat.JPEG, QUALITY, bytes)
        if (out !== src) out.recycle()
        Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP)
    } catch (e: Throwable) {
        FileLog.w(TAG, "encode failed: ${e.message}")
        null
    } finally {
        src.recycle()
    }

    /** Size with the longest side at most [MAX_SIDE], keeping the aspect ratio. */
    fun scaled(w: Int, h: Int): Pair<Int, Int> {
        val longest = maxOf(w, h)
        if (longest <= MAX_SIDE) return w to h
        val f = MAX_SIDE.toDouble() / longest
        return maxOf(1, Math.round(w * f).toInt()) to maxOf(1, Math.round(h * f).toInt())
    }
}
