package com.aisignx.player.cache

import com.aisignx.player.FileLog
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Background downloads into [MediaCache]: at most [parallel] at a time, videos first,
 * up to [attempts] tries with exponential backoff. Each download sends the WebView's
 * cookie (the display credential), writes a .part file, hashes while writing and lets
 * MediaCache verify the exact size and SHA-256 before the file is renamed into place.
 */
class MediaDownloader(
    private val cache: MediaCache,
    private val origin: String,
    private val client: OkHttpClient,
    private val cookieFor: (String) -> String?,
    private val parallel: Int = 2,
    private val attempts: Int = 3,
    private val backoffMs: Long = 2_000L,
) {
    private class Job(val path: String, val video: Boolean, val seq: Long)

    private val pending = LinkedHashMap<String, Job>()
    private val running = HashSet<String>()
    private var seq = 0L
    @Volatile private var closed = false
    private val pool: ExecutorService = Executors.newFixedThreadPool(parallel) { r ->
        Thread(r, "aisignx-media").apply { isDaemon = true; priority = Thread.MIN_PRIORITY }
    }

    /** Queue [path] unless it is cached, queued or downloading. */
    fun enqueue(path: String, video: Boolean) {
        synchronized(this) {
            if (closed || cache.isCached(path) || path in pending || path in running) return
            pending[path] = Job(path, video, seq++)
        }
        try { pool.execute(::runNext) } catch (_: Exception) { /* shut down */ }
    }

    fun shutdown() {
        synchronized(this) { closed = true; pending.clear() }
        pool.shutdownNow()
    }

    /** For tests: wait until the queue is empty and nothing is downloading. */
    fun awaitIdle(timeoutMs: Long): Boolean {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            synchronized(this) { if (pending.isEmpty() && running.isEmpty()) return true }
            Thread.sleep(20)
        }
        return false
    }

    fun awaitTermination(ms: Long) = pool.awaitTermination(ms, TimeUnit.MILLISECONDS)

    private fun runNext() {
        val job = synchronized(this) {
            // Videos first, then oldest first.
            val next = pending.values.minWithOrNull(compareBy<Job>({ !it.video }, { it.seq })) ?: return
            pending.remove(next.path)
            running.add(next.path)
            next
        }
        try {
            for (attempt in 1..attempts) {
                if (closed) return
                when (download(job.path)) {
                    Outcome.DONE, Outcome.GIVE_UP -> return
                    Outcome.RETRY -> if (attempt < attempts) Thread.sleep(backoffMs shl (attempt - 1))
                }
            }
            FileLog.w(TAG, "giving up on ${job.path} after $attempts attempts")
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        } finally {
            synchronized(this) { running.remove(job.path) }
        }
    }

    private enum class Outcome { DONE, RETRY, GIVE_UP }

    private fun download(path: String): Outcome {
        if (cache.isCached(path)) return Outcome.DONE
        val url = origin + path
        // identity: the exact byte count is verified against the manifest size.
        val req = Request.Builder().url(url).get().header("Accept-Encoding", "identity").apply {
            cookieFor(url)?.takeIf { it.isNotEmpty() }?.let { header("Cookie", it) }
        }.build()
        val part = cache.partFileFor(path)
        try {
            client.newCall(req).execute().use { resp ->
                if (resp.code != 200) {
                    FileLog.w(TAG, "download $path: HTTP ${resp.code}")
                    // 4xx other than 408/429 will not change by retrying now.
                    return if (resp.code in 400..499 && resp.code != 408 && resp.code != 429) Outcome.GIVE_UP
                    else Outcome.RETRY
                }
                val body = resp.body ?: return Outcome.RETRY
                val digest = MessageDigest.getInstance("SHA-256")
                body.byteStream().use { input ->
                    part.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        while (true) {
                            if (closed) throw IOException("shut down")
                            val n = input.read(buf)
                            if (n < 0) break
                            digest.update(buf, 0, n)
                            out.write(buf, 0, n)
                        }
                    }
                }
                val sha = digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
                val length = body.contentLength().takeIf { it >= 0 }
                val ok = cache.commit(path, part, sha, length, resp.header("Content-Type"))
                if (ok) FileLog.i(TAG, "cached $path (${cache.expectedFor(path)?.size ?: length} bytes)")
                return if (ok) Outcome.DONE else Outcome.RETRY
            }
        } catch (e: IOException) {
            part.delete()
            FileLog.w(TAG, "download $path failed: ${e.javaClass.simpleName}")
            return Outcome.RETRY
        } catch (e: RuntimeException) {
            part.delete()
            FileLog.e(TAG, "download $path crashed", e)
            return Outcome.GIVE_UP
        }
    }

    companion object {
        private const val TAG = "MediaDownloader"
    }
}
