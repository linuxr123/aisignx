package com.aisignx.player.cache

import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.TimeUnit

class PageCacheTest {

    @get:Rule val tmp = TemporaryFolder()
    private lateinit var server: MockWebServer
    private lateinit var dir: File
    private var clock = 1_000_000L
    private val client = OkHttpClient.Builder().connectTimeout(1, TimeUnit.SECONDS).build()

    private fun page(build: String) = """<!doctype html><link rel="stylesheet" href="/static/player/player.css?v=$build">
        <script src="/static/player/net.js?v=$build"></script>"""

    private val csp = "default-src 'self'"

    @Before fun setUp() {
        server = MockWebServer()
        server.start()
        dir = tmp.newFolder("page-cache")
    }

    @After fun tearDown() = try { server.shutdown() } catch (_: Exception) { }

    private fun cache() = PageCache(dir, server.url("/").toString().trimEnd('/'), client) { clock }

    private fun html(build: String) = MockResponse()
        .setHeader("Content-Type", "text/html; charset=utf-8")
        .setHeader("Content-Security-Policy", csp)
        .setHeader("X-Frame-Options", "DENY")
        .setBody(page(build))

    private fun js(body: String) = MockResponse().setHeader("Content-Type", "text/javascript").setBody(body)

    private fun Served.text() = body.readBytes().toString(Charsets.UTF_8)

    @Test fun servesNetworkThenSnapshotWhenServerIsDown() {
        val c = cache()
        server.enqueue(html("1"))
        server.enqueue(js("css"))
        server.enqueue(js("net-1"))
        val live = c.fetch("/player/")!!
        assertEquals(200, live.status)
        assertEquals("text/html", live.mime)
        assertEquals("utf-8", live.charset)
        assertNull(live.headers[PageCache.CACHE_HEADER])
        assertTrue(live.text().contains("net.js?v=1"))
        assertEquals("css", c.fetch("/static/player/player.css?v=1")!!.text())
        assertEquals("net-1", c.fetch("/static/player/net.js?v=1")!!.text())

        server.shutdown()
        val snap = c.fetch("/player/")!!
        assertEquals(200, snap.status)
        assertEquals("1", snap.headers[PageCache.CACHE_HEADER])
        assertEquals(csp, snap.headers["Content-Security-Policy"])
        assertEquals("DENY", snap.headers["X-Frame-Options"])
        assertTrue(snap.text().contains("net.js?v=1"))
        assertEquals("net-1", c.fetch("/static/player/net.js?v=1")!!.text())
        // Nothing cached for this one: let the WebView fail natively.
        assertNull(c.fetch("/static/player/render.js?v=1"))
    }

    @Test fun badGatewayServesSnapshot() {
        val c = cache()
        server.enqueue(html("1"))
        c.fetch("/player/")
        server.enqueue(MockResponse().setResponseCode(502))
        val s = c.fetch("/player/")!!
        assertEquals(200, s.status)
        assertEquals("1", s.headers[PageCache.CACHE_HEADER])
    }

    @Test fun otherErrorsPassThroughAndAreNotStored() {
        val c = cache()
        server.enqueue(MockResponse().setResponseCode(404).setBody("nope"))
        val s = c.fetch("/static/player/missing.js")!!
        assertEquals(404, s.status)
        assertEquals(0, dir.listFiles()!!.size)
    }

    @Test fun noSnapshotAndServerDownReturnsNull() {
        val c = cache()
        server.shutdown()
        assertNull(c.fetch("/player/"))
    }

    @Test fun oldBuildIsPrunedOnceNewBuildIsComplete() {
        val c = cache()
        server.enqueue(html("1")); server.enqueue(js("css1")); server.enqueue(js("net-1"))
        c.fetch("/player/"); c.fetch("/static/player/player.css?v=1"); c.fetch("/static/player/net.js?v=1")
        val old = File(dir, PageCache.keyFor("/static/player/net.js?v=1") + ".body")
        assertTrue(old.isFile)

        server.enqueue(html("2")); server.enqueue(js("css2")); server.enqueue(js("net-2"))
        c.fetch("/player/")
        c.fetch("/static/player/player.css?v=2")
        assertTrue("kept until the new build is complete", old.isFile)
        c.fetch("/static/player/net.js?v=2")
        assertFalse(old.isFile)
        assertNotNull(File(dir, PageCache.keyFor("/static/player/net.js?v=2") + ".body").takeIf { it.isFile })
    }

    @Test fun handlesOnlyPlayerPaths() {
        val c = cache()
        assertTrue(c.handles("/player/"))
        assertTrue(c.handles("/static/player/net.js"))
        assertFalse(c.handles("/player"))
        assertFalse(c.handles("/api/player/v1/manifest"))
        assertFalse(c.handles("/static/admin.js"))
    }

    @Test fun referencedPathsDecodesEntities() {
        val refs = PageCache.referencedPaths("""<script src="/static/player/a.js?v=1&amp;x=2"></script><img src="/other.png">""")
        assertEquals(setOf("/static/player/a.js?v=1&x=2"), refs)
    }
}
