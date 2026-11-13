package com.aisignx.player.cache

import com.aisignx.player.FileLog
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.net.URI
import java.security.MessageDigest

/**
 * Cache-first store for the player's image and video files
 * (/api/player/v1/media/<id>/<name>, immutable on the server).
 *
 * Files are <dir>/<sha1(path)>.bin with index.json {path: {size, sha256, mime, lastUsed}}.
 * A hit is streamed (200, or 206 for a Range) and never buffered in memory. A miss
 * returns null so the WebView streams from the server itself, and a full download is
 * queued in the background. Eviction is LRU, runs only above the budget and never
 * touches files in the current manifest; earlier schedules' media may play again,
 * so nothing is deleted just for being unreferenced.
 *
 * Downloads that would not fit (current manifest over budget, or the volume would drop
 * below [reserve] free bytes) are skipped; the WebView streams those items instead.
 */
class MediaCache(
    val dir: File,
    private val budget: (File) -> Long = ::defaultBudget,
    private val now: () -> Long = System::currentTimeMillis,
    private val freeSpace: (File) -> Long = { it.usableSpace },
    val reserve: Long = DEFAULT_RESERVE,
) {
    data class Entry(val size: Long, val sha256: String?, val mime: String, val lastUsed: Long)

    /** What the newest manifest says about a file. */
    data class Expected(val size: Long?, val sha256: String?, val mime: String?, val video: Boolean)

    private val index = LinkedHashMap<String, Entry>()
    private val expected = HashMap<String, Expected>()
    private var current: Set<String> = emptySet()

    /** Set by the owner; receives paths that should be downloaded. */
    @Volatile var onMissing: ((path: String, video: Boolean) -> Unit)? = null

    init {
        dir.mkdirs()
        load()
    }

    fun handles(path: String): Boolean = MEDIA_PATH.matches(path)

    /** Serve a cached file, or null on a miss (the miss is queued for download). */
    fun serve(path: String, rangeHeader: String?): Served? {
        val entry = touch(path)
        if (entry == null) {
            val exp = synchronized(this) { expected[path] }
            onMissing?.invoke(path, exp?.video ?: guessVideo(path))
            return null
        }
        val file = fileFor(path)
        val total = entry.size
        val base = linkedMapOf("Accept-Ranges" to "bytes", "Cache-Control" to "no-store")
        return try {
            when (val r = Range.parse(rangeHeader, total)) {
                is Range.Result.Full -> {
                    base["Content-Length"] = total.toString()
                    Served(200, "OK", entry.mime, null, base, FileInputStream(file))
                }
                is Range.Result.Partial -> {
                    val raf = RandomAccessFile(file, "r")
                    raf.seek(r.start)
                    base["Content-Length"] = r.length.toString()
                    base["Content-Range"] = Range.contentRange(r, total)
                    Served(206, "Partial Content", entry.mime, null, base, BoundedFileInputStream(raf, r.length))
                }
                is Range.Result.Unsatisfiable -> {
                    base["Content-Range"] = Range.unsatisfiedRange(total)
                    base["Content-Length"] = "0"
                    Served(416, "Range Not Satisfiable", entry.mime, null, base, ByteArray(0).inputStream())
                }
            }
        } catch (e: IOException) {
            FileLog.w(TAG, "cached file unreadable, dropping: ${e.message}")
            drop(path)
            null
        }
    }

    /**
     * Record the manifest the player applied (the JSON the page passed to the shell
     * hook). Returns the image/video paths it references; missing ones are queued.
     */
    fun onManifest(json: String): List<String> {
        val items: JSONArray = try {
            JSONObject(json).optJSONArray("items") ?: JSONArray()
        } catch (e: Exception) {
            FileLog.w(TAG, "manifest not parsed: ${e.message}")
            return emptyList()
        }
        val paths = ArrayList<String>()
        val missing = ArrayList<Pair<String, Boolean>>()
        synchronized(this) {
            for (i in 0 until items.length()) {
                val item = items.optJSONObject(i) ?: continue
                val type = item.optString("type")
                if (type != "image" && type != "video") continue
                val path = pathOf(item.optString("url")) ?: continue
                if (!handles(path)) continue
                val size = if (item.isNull("size")) null else item.optLong("size", -1L).takeIf { it >= 0 }
                val sha = item.optString("sha256").lowercase().takeIf { SHA_RE.matches(it) }
                val mime = item.optString("mime").takeIf { it.isNotEmpty() && it != "null" }
                expected[path] = Expected(size, sha, mime, type == "video")
                paths.add(path)
                if (!index.containsKey(path)) missing.add(path to (type == "video"))
            }
            current = paths.toSet()
        }
        // Videos first: they take longest and matter most offline.
        missing.sortedBy { !it.second }.forEach { (p, v) -> onMissing?.invoke(p, v) }
        return paths
    }

    @Synchronized fun isCached(path: String): Boolean = index.containsKey(path)

    @Synchronized fun expectedFor(path: String): Expected? = expected[path]

    /** Temporary download target for [path]. */
    fun partFileFor(path: String): File = File(dir, key(path) + ".part")

    /**
     * Verify a finished download and move it into the cache. The size must equal the
     * manifest size (or [contentLength] when the manifest gave none); the SHA-256
     * must match when the manifest gave one. A mismatch deletes the part file.
     */
    fun commit(path: String, part: File, sha256: String, contentLength: Long?, mime: String?): Boolean {
        val exp = expectedFor(path)
        val want = exp?.size ?: contentLength
        val actual = part.length()
        if (want == null || actual != want) {
            FileLog.w(TAG, "size mismatch for $path: got $actual, expected $want")
            part.delete()
            return false
        }
        if (exp?.sha256 != null && exp.sha256 != sha256.lowercase()) {
            FileLog.w(TAG, "sha256 mismatch for $path")
            part.delete()
            return false
        }
        val target = fileFor(path)
        synchronized(this) {
            target.delete()
            if (!part.renameTo(target)) {
                part.delete()
                return false
            }
            val type = exp?.mime ?: mime?.substringBefore(';')?.trim()?.ifEmpty { null } ?: guessMime(path)
            index[path] = Entry(actual, sha256.lowercase(), type, now())
            evictLocked()
            saveLocked()
        }
        return true
    }

    /** LRU eviction down to the budget, never evicting files in the current manifest. */
    @Synchronized fun evict() { evictLocked(); saveLocked() }

    /**
     * Whether a [size]-byte download of [path] fits: the current manifest's cached files
     * plus this one stay within the budget, and the volume keeps [reserve] bytes free
     * (counting space that evicting files outside the current manifest would free).
     */
    @Synchronized fun hasRoomFor(path: String, size: Long): Boolean {
        var pinned = 0L
        var evictable = 0L
        for ((p, e) in index) {
            if (p == path) continue
            if (p in current) pinned += e.size else evictable += e.size
        }
        if (pinned + size > budget(dir)) return false
        return freeSpace(dir) + evictable - size >= reserve
    }

    /** True once the cache's volume is below the free-space reserve. */
    fun isLowOnSpace(): Boolean = freeSpace(dir) < reserve

    @Synchronized fun totalBytes(): Long = index.values.sumOf { it.size }

    /** Delete every cached file and the index (server changed or forgotten). */
    @Synchronized fun clear() {
        index.clear(); expected.clear(); current = emptySet()
        dir.listFiles()?.forEach { it.delete() }
    }

    private fun evictLocked() {
        val limit = budget(dir)
        var total = index.values.sumOf { it.size }
        if (total <= limit) return
        val victims = index.entries.filter { it.key !in current }.sortedBy { it.value.lastUsed }
        for ((path, entry) in victims) {
            if (total <= limit) break
            fileFor(path).delete()
            index.remove(path)
            total -= entry.size
            FileLog.i(TAG, "evicted $path (${entry.size} bytes)")
        }
    }

    @Synchronized private fun touch(path: String): Entry? {
        val e = index[path] ?: return null
        if (!fileFor(path).isFile) { index.remove(path); saveLocked(); return null }
        val t = now()
        if (t - e.lastUsed > TOUCH_SAVE_MS) {
            index[path] = e.copy(lastUsed = t)
            saveLocked()
        }
        return e
    }

    @Synchronized private fun drop(path: String) {
        index.remove(path)
        fileFor(path).delete()
        saveLocked()
    }

    private fun fileFor(path: String) = File(dir, key(path) + ".bin")

    private fun load() {
        val f = File(dir, INDEX)
        if (f.isFile) {
            try {
                val obj = JSONObject(f.readText(Charsets.UTF_8))
                for (path in obj.keys()) {
                    val o = obj.optJSONObject(path) ?: continue
                    val e = Entry(o.optLong("size"), o.optString("sha256").ifEmpty { null },
                        o.optString("mime").ifEmpty { guessMime(path) }, o.optLong("lastUsed"))
                    if (fileFor(path).length() == e.size && e.size > 0) index[path] = e
                }
            } catch (e: Exception) {
                FileLog.w(TAG, "index unreadable, starting empty: ${e.message}")
            }
        }
        // Remove partial downloads and files the index does not know.
        val known = index.keys.map { key(it) + ".bin" }.toSet()
        dir.listFiles()?.forEach { if (it.name != INDEX && it.name !in known) it.delete() }
        saveLocked()
    }

    private fun saveLocked() {
        val obj = JSONObject()
        for ((path, e) in index) {
            obj.put(path, JSONObject().put("size", e.size).put("sha256", e.sha256 ?: "")
                .put("mime", e.mime).put("lastUsed", e.lastUsed))
        }
        try {
            PageCache.writeAtomic(File(dir, INDEX), obj.toString().toByteArray(Charsets.UTF_8))
        } catch (e: IOException) {
            FileLog.w(TAG, "index write failed: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "MediaCache"
        private const val INDEX = "index.json"
        private const val TOUCH_SAVE_MS = 60_000L
        private const val MAX_BUDGET = 4L * 1024 * 1024 * 1024
        /** Free space always left on the volume for cookies, settings, logs and snapshots. */
        const val DEFAULT_RESERVE = 256L * 1024 * 1024
        val MEDIA_PATH = Regex("^/api/player/v1/media/\\d+/[^/]+$")
        private val SHA_RE = Regex("^[0-9a-f]{64}$")

        /** min(4 GB, 50% of the free space on the cache's volume). */
        fun defaultBudget(dir: File): Long = minOf(MAX_BUDGET, dir.usableSpace / 2)

        fun key(path: String): String =
            MessageDigest.getInstance("SHA-1").digest(path.toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it.toInt() and 0xff) }

        /** The path of a manifest URL ("/api/..." or an absolute URL), without query. */
        fun pathOf(url: String): String? {
            if (url.isEmpty()) return null
            if (url.startsWith("/") && !url.startsWith("//")) return url.substringBefore('?').substringBefore('#')
            return try { URI(url).rawPath?.takeIf { it.startsWith("/") } } catch (_: Exception) { null }
        }

        fun guessVideo(path: String): Boolean =
            path.substringAfterLast('.', "").lowercase() in setOf("mp4", "webm", "mov", "m4v", "mkv", "ogv")

        fun guessMime(path: String): String = when (path.substringAfterLast('.', "").lowercase()) {
            "mp4", "m4v" -> "video/mp4"
            "webm" -> "video/webm"
            "mov" -> "video/quicktime"
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "gif" -> "image/gif"
            "webp" -> "image/webp"
            "svg" -> "image/svg+xml"
            else -> "application/octet-stream"
        }
    }
}
