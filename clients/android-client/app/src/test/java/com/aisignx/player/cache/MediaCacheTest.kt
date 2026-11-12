package com.aisignx.player.cache

import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest

class MediaCacheTest {

    @get:Rule val tmp = TemporaryFolder()
    private var clock = 1_000L

    private fun sha(b: ByteArray) =
        MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun cache(dir: File = tmp.root, budget: Long = Long.MAX_VALUE) =
        MediaCache(dir, budget = { budget }, now = { clock })

    private fun path(id: Int, ext: String = "mp4") = "/api/player/v1/media/$id/f$id.$ext"

    private fun manifest(vararg items: Triple<String, ByteArray?, String>): String {
        val arr = JSONArray()
        items.forEachIndexed { i, (p, data, type) ->
            arr.put(JSONObject().put("id", i).put("type", type).put("url", p)
                .put("size", data?.size ?: JSONObject.NULL)
                .put("sha256", data?.let { sha(it) } ?: JSONObject.NULL)
                .put("mime", if (type == "video") "video/mp4" else "image/png"))
        }
        arr.put(JSONObject().put("type", "web").put("url", "https://example.com/"))
        return JSONObject().put("v", 1).put("items", arr).toString()
    }

    /** Write [data] to the part file and commit it, as the downloader does. */
    private fun put(c: MediaCache, p: String, data: ByteArray, contentLength: Long? = data.size.toLong()): Boolean {
        val part = c.partFileFor(p)
        part.writeBytes(data)
        return c.commit(p, part, sha(data), contentLength, "video/mp4")
    }

    @Test fun commitRenamesPartAndServesHit() {
        val c = cache()
        val data = ByteArray(1000) { it.toByte() }
        assertNull("miss returns null", c.serve(path(1), null))
        assertTrue(put(c, path(1), data))
        assertFalse(c.partFileFor(path(1)).exists())

        val full = c.serve(path(1), null)!!
        assertEquals(200, full.status)
        assertEquals("1000", full.headers["Content-Length"])
        assertEquals("bytes", full.headers["Accept-Ranges"])
        assertTrue(full.body.use { it.readBytes() }.contentEquals(data))

        val part = c.serve(path(1), "bytes=100-199")!!
        assertEquals(206, part.status)
        assertEquals("bytes 100-199/1000", part.headers["Content-Range"])
        assertEquals("100", part.headers["Content-Length"])
        assertTrue(part.body.use { it.readBytes() }.contentEquals(data.copyOfRange(100, 200)))

        assertEquals(416, c.serve(path(1), "bytes=5000-")!!.status)
    }

    @Test fun sizeMismatchIsDiscarded() {
        val c = cache()
        val data = ByteArray(100)
        c.onManifest(manifest(Triple(path(1), ByteArray(200), "video")))
        assertFalse(put(c, path(1), data))
        assertFalse(c.isCached(path(1)))
        assertFalse(c.partFileFor(path(1)).exists())
    }

    @Test fun missingLengthIsDiscarded() {
        val c = cache()
        assertFalse(put(c, path(1), ByteArray(10), contentLength = null))
        assertFalse(c.isCached(path(1)))
    }

    @Test fun shaMismatchIsDiscarded() {
        val c = cache()
        val expected = ByteArray(100) { 1 }
        c.onManifest(manifest(Triple(path(1), expected, "image")))
        assertFalse(put(c, path(1), ByteArray(100) { 2 }))
        assertFalse(c.isCached(path(1)))
        assertTrue(put(c, path(1), expected))
        assertTrue(c.isCached(path(1)))
    }

    @Test fun indexSurvivesRestartAndStrayFilesAreRemoved() {
        val dir = tmp.newFolder("m")
        val c = cache(dir)
        assertTrue(put(c, path(1), ByteArray(50)))
        File(dir, "stray.part").writeText("x")
        val again = cache(dir)
        assertTrue(again.isCached(path(1)))
        assertFalse(File(dir, "stray.part").exists())
    }

    @Test fun evictionIsLruAndNeverTouchesCurrentManifest() {
        val c = cache(budget = 250)
        val a = ByteArray(100) { 1 }; val b = ByteArray(100) { 2 }; val d = ByteArray(100) { 3 }
        clock = 1_000; assertTrue(put(c, path(1), a))
        clock = 2_000; assertTrue(put(c, path(2), b))
        // Path 1 is in the current manifest; path 2 is not and is the oldest unpinned.
        c.onManifest(manifest(Triple(path(1), a, "video"), Triple(path(3), d, "video")))
        clock = 3_000; assertTrue(put(c, path(3), d))
        assertTrue(c.isCached(path(1)))
        assertFalse(c.isCached(path(2)))
        assertTrue(c.isCached(path(3)))
        assertEquals(200L, c.totalBytes())
    }

    @Test fun noEvictionUnderBudget() {
        val c = cache(budget = 1_000)
        assertTrue(put(c, path(1), ByteArray(100)))
        c.onManifest(manifest(Triple(path(2), null, "video")))
        assertTrue("unreferenced files stay while under budget", c.isCached(path(1)))
    }

    @Test fun manifestQueuesMissingFilesOnly() {
        val c = cache()
        assertTrue(put(c, path(1), ByteArray(10)))
        val queued = ArrayList<Pair<String, Boolean>>()
        c.onMissing = { p, v -> queued.add(p to v) }
        val paths = c.onManifest(manifest(Triple(path(1), ByteArray(10), "video"),
            Triple(path(2, "png"), ByteArray(5), "image")))
        assertEquals(listOf(path(1), path(2, "png")), paths)
        assertEquals(listOf(path(2, "png") to false), queued)
        assertEquals(emptyList<String>(), c.onManifest("not json"))
    }

    @Test fun handlesOnlyMediaPaths() {
        val c = cache()
        assertTrue(c.handles("/api/player/v1/media/12/abc.mp4"))
        assertFalse(c.handles("/api/player/v1/manifest"))
        assertFalse(c.handles("/api/player/v1/media/x/abc.mp4"))
        assertFalse(c.handles("/static/player/net.js"))
    }

    @Test fun downloaderVerifiesAndSendsCookie() {
        val server = MockWebServer()
        val good = ByteArray(4096) { (it % 251).toByte() }
        server.enqueue(MockResponse().setHeader("Content-Type", "video/mp4").setBody(Buffer().write(good)))
        server.enqueue(MockResponse().setHeader("Content-Type", "image/png").setBody(Buffer().write(ByteArray(10))))
        server.start()
        try {
            val origin = server.url("/").toString().trimEnd('/')
            val c = cache()
            val dl = MediaDownloader(c, origin, OkHttpClient(), { "aisignx_display=secret" },
                parallel = 1, attempts = 1, backoffMs = 1)
            c.onMissing = { p, v -> dl.enqueue(p, v) }
            // The image is listed first, but the video is queued first.
            // The manifest promises 20 bytes for the image, the server sends 10: discarded.
            c.onManifest(manifest(Triple(path(2, "png"), ByteArray(20), "image"), Triple(path(1), good, "video")))
            assertTrue(dl.awaitIdle(10_000))
            dl.shutdown()

            assertTrue(c.isCached(path(1)))
            assertFalse(c.isCached(path(2, "png")))
            val first = server.takeRequest()
            assertEquals("video is downloaded first", path(1), first.path)
            assertEquals("aisignx_display=secret", first.getHeader("Cookie"))
            assertNotNull(c.serve(path(1), null))
            assertEquals(0, tmp.root.listFiles()!!.count { it.name.endsWith(".part") })
        } finally {
            server.shutdown()
        }
    }
}
