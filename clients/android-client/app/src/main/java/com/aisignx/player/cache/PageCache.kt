package com.aisignx.player.cache

import com.aisignx.player.FileLog
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Network-first snapshot of the player page (/player/) and its scripts and styles
 * (/static/player/...), so the player can cold-start while the server is down.
 *
 * A 200 response is stored as <sha1(path+query)>.body plus a .meta JSON with the
 * status, content type, CSP and X-Frame-Options. On a network error, a timeout or any
 * 5xx the snapshot is served with "X-AiSignX-Shell-Cache: 1".
 *
 * A new /player/ page is first stored as pending and becomes the served snapshot only
 * once every /static/player/ file it references is stored too, so a cold start never
 * gets a page whose scripts are missing. Snapshots that neither the served nor the
 * pending page references (old ?v= builds) are pruned.
 *
 * These URLs carry no credential (the display cookie is scoped to /api/player), so
 * no cookie is sent or stored here.
 */
class PageCache(
    private val dir: File,
    private val origin: String,
    client: OkHttpClient,
    private val now: () -> Long = System::currentTimeMillis,
) {
    private val client = client.newBuilder().callTimeout(TIMEOUT_S, TimeUnit.SECONDS).build()

    /** After a network failure, serve snapshots directly for a short while. */
    @Volatile private var offlineUntil = 0L

    init { dir.mkdirs() }

    /** Whether [path] (without query) is handled by this cache. */
    fun handles(path: String): Boolean = path == PLAYER_PATH || path.startsWith(STATIC_PREFIX)

    /**
     * The response for a GET of [pathAndQuery] (for example "/static/player/net.js?v=abc"),
     * or null to let the WebView try the network itself (no snapshot to fall back on).
     */
    fun fetch(pathAndQuery: String): Served? {
        val key = keyFor(pathAndQuery)
        if (now() < offlineUntil) {
            snapshot(key)?.let { return it }
        }
        val req = Request.Builder().url(origin + pathAndQuery).get().build()
        try {
            client.newCall(req).execute().use { resp ->
                if (resp.code >= 500) {
                    snapshot(key)?.let { return it }
                }
                val body = resp.body?.bytes() ?: ByteArray(0)
                if (resp.code == 200) save(key, resp, body, pathAndQuery)
                offlineUntil = 0L
                return fromNetwork(resp, body)
            }
        } catch (e: IOException) {
            offlineUntil = now() + OFFLINE_MEMO_MS
            FileLog.w(TAG, "network failed for ${pathAndQuery.substringBefore('?')}: ${e.javaClass.simpleName}")
            return snapshot(key)
        }
    }

    /** Delete every snapshot (server changed or forgotten). */
    fun clear() {
        dir.listFiles()?.forEach { it.delete() }
    }

    private fun fromNetwork(resp: Response, body: ByteArray): Served {
        val (mime, charset) = splitType(resp.header("Content-Type"))
        val headers = linkedMapOf("Cache-Control" to "no-store")
        resp.header("Content-Security-Policy")?.let { headers["Content-Security-Policy"] = it }
        resp.header("X-Frame-Options")?.let { headers["X-Frame-Options"] = it }
        resp.header("X-Content-Type-Options")?.let { headers["X-Content-Type-Options"] = it }
        val reason = resp.message.ifBlank { if (resp.code < 400) "OK" else "Error" }
        return Served(resp.code, reason, mime, charset, headers, ByteArrayInputStream(body))
    }

    @Synchronized
    private fun save(key: String, resp: Response, body: ByteArray, pathAndQuery: String) {
        try {
            val meta = JSONObject()
                .put("path", pathAndQuery)
                .put("status", 200)
                .put("type", resp.header("Content-Type") ?: "application/octet-stream")
                .put("csp", resp.header("Content-Security-Policy") ?: "")
                .put("xfo", resp.header("X-Frame-Options") ?: "")
            // The page waits as pending until its scripts and styles are stored.
            val name = if (key == keyFor(PLAYER_PATH)) "$key$PENDING" else key
            writeAtomic(File(dir, "$name.body"), body)
            writeAtomic(File(dir, "$name.meta"), meta.toString().toByteArray(Charsets.UTF_8))
            promote()
            prune()
        } catch (e: IOException) {
            FileLog.w(TAG, "snapshot write failed: ${e.message}")
        }
    }

    @Synchronized
    private fun snapshot(key: String): Served? {
        val bodyFile = File(dir, "$key.body")
        val metaFile = File(dir, "$key.meta")
        if (!bodyFile.isFile || !metaFile.isFile) return null
        return try {
            val meta = JSONObject(metaFile.readText(Charsets.UTF_8))
            val (mime, charset) = splitType(meta.optString("type"))
            val headers = linkedMapOf("Cache-Control" to "no-store", CACHE_HEADER to "1")
            meta.optString("csp").takeIf { it.isNotEmpty() }?.let { headers["Content-Security-Policy"] = it }
            meta.optString("xfo").takeIf { it.isNotEmpty() }?.let { headers["X-Frame-Options"] = it }
            Served(200, "OK", mime, charset, headers, ByteArrayInputStream(bodyFile.readBytes()))
        } catch (e: Exception) {
            FileLog.w(TAG, "snapshot unreadable: ${e.message}")
            null
        }
    }

    /** Keys of the files [pageFile] references, or null when it does not exist. */
    private fun referencedKeys(pageFile: File): Set<String>? =
        if (pageFile.isFile) referencedPaths(pageFile.readText(Charsets.UTF_8)).map { keyFor(it) }.toSet() else null

    /** Make the pending page the served snapshot once everything it references is stored. */
    private fun promote() {
        val pageKey = keyFor(PLAYER_PATH)
        val refs = referencedKeys(File(dir, "$pageKey$PENDING.body")) ?: return
        if (refs.any { !File(dir, "$it.body").isFile }) return
        File(dir, "$pageKey$PENDING.meta").let { if (it.isFile) writeAtomic(File(dir, "$pageKey.meta"), it.readBytes()) }
        writeAtomic(File(dir, "$pageKey.body"), File(dir, "$pageKey$PENDING.body").readBytes())
        File(dir, "$pageKey$PENDING.body").delete()
        File(dir, "$pageKey$PENDING.meta").delete()
    }

    /**
     * Keep the served /player/ snapshot, the pending one and what both reference.
     * Prune only once every file the served page references is cached, so an
     * interrupted update never leaves the page without its scripts.
     */
    private fun prune() {
        val pageKey = keyFor(PLAYER_PATH)
        val served = referencedKeys(File(dir, "$pageKey.body")) ?: return
        if (served.any { !File(dir, "$it.body").isFile }) return
        val keep = served.toMutableSet()
        referencedKeys(File(dir, "$pageKey$PENDING.body"))?.let { keep.addAll(it) }
        keep.add(pageKey)
        keep.add("$pageKey$PENDING")
        dir.listFiles()?.forEach { f ->
            val k = f.name.substringBefore('.')
            if (k !in keep) f.delete()
        }
    }

    companion object {
        private const val TAG = "PageCache"
        const val PLAYER_PATH = "/player/"
        const val STATIC_PREFIX = "/static/player/"
        const val CACHE_HEADER = "X-AiSignX-Shell-Cache"
        private const val TIMEOUT_S = 5L
        private const val OFFLINE_MEMO_MS = 10_000L
        private const val PENDING = "-pending"
        private val REF = Regex("""(?:src|href)\s*=\s*"([^"]+)"""")

        fun keyFor(pathAndQuery: String): String =
            MessageDigest.getInstance("SHA-1").digest(pathAndQuery.toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it.toInt() and 0xff) }

        /** Same-origin /static/player/ references in the page, HTML entities decoded. */
        fun referencedPaths(html: String): Set<String> =
            REF.findAll(html).map { it.groupValues[1].replace("&amp;", "&") }
                .filter { it.startsWith(STATIC_PREFIX) }
                .toSet()

        fun splitType(contentType: String?): Pair<String, String?> {
            val raw = contentType?.takeIf { it.isNotBlank() } ?: return "application/octet-stream" to null
            val mime = raw.substringBefore(';').trim().ifEmpty { "application/octet-stream" }
            val charset = raw.split(';').drop(1).map { it.trim() }
                .firstOrNull { it.startsWith("charset=", ignoreCase = true) }
                ?.substringAfter('=')?.trim('"', ' ')
            return mime to charset
        }

        fun writeAtomic(target: File, bytes: ByteArray) {
            val tmp = File(target.parentFile, target.name + ".tmp")
            tmp.writeBytes(bytes)
            if (!tmp.renameTo(target)) {
                target.delete()
                if (!tmp.renameTo(target)) throw IOException("rename failed for ${target.name}")
            }
        }
    }
}
