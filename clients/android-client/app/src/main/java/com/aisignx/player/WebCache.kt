package com.aisignx.player

import android.content.Context
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import okhttp3.Cache
import okhttp3.CacheControl
import okhttp3.Headers
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.ByteArrayInputStream
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * WebCache Ã¢â‚¬â€ disk-backed cache + request router for the player WebView.
 *
 * Mirrors the strategy used by static/sw.js in the browser client:
 *   /static/...           Ã¢â€ â€™ cache-first  (long TTL; SW would be cache-first too)
 *   /uploads/...          Ã¢â€ â€™ cache-first  (immutable UUID URLs)
 *   /plugin_assets/...    Ã¢â€ â€™ stale-while-revalidate
 *   /plugin/<type>?cfg= Ã¢â€ â€™ stale-while-revalidate (iframe wrapper HTML)
 *   /display/<token>    Ã¢â€ â€™ network-first, cache fallback
 *   anything else       Ã¢â€ â€™ network only (API writes, SSE, ping)
 *
 * The OkHttp cache lives at <cacheDir>/web-cache and survives app restarts.
 * Default ceiling is 100 MB which is plenty for a typical signage playlist
 * of cached video + image + plugin asset bundles.
 *
 * The cache is invoked via WebViewClient.shouldInterceptRequest in
 * PlayerActivity. Anything that returns null falls through to the WebView's
 * own networking, so it's safe to be conservative Ã¢â‚¬â€ we only intercept GETs
 * for paths we know are safe to cache.
 */
object WebCache {

	private const val TAG = "AISignX/WebCache"
	private const val CACHE_DIR_NAME = "web-cache"
	private const val CACHE_SIZE_BYTES = 300L * 1024 * 1024   // 300 MB
	private const val PREFETCH_CONCURRENCY = 6

	private var client: OkHttpClient? = null
	private var mediaDownloadClient: OkHttpClient? = null

	/** Lazy-init: cache directory only exists once Context is available. */
	@Synchronized
	fun client(ctx: Context): OkHttpClient {
		client?.let { return it }
		val cacheDir = File(ctx.cacheDir, CACHE_DIR_NAME).apply { mkdirs() }
		val c = LanHttps.apply(OkHttpClient.Builder())
			.cache(Cache(cacheDir, CACHE_SIZE_BYTES))
			// Aggressive timeouts -- when the server is unreachable we want
			// to fall back to the cache fast, NOT block the WebView for 15
			// seconds while a TCP SYN times out. The WebView itself may also
			// abandon the resource request before we'd ever get past a
			// 15-second connect timeout, leaving us with nothing on screen.
			.connectTimeout(3, TimeUnit.SECONDS)
			.readTimeout(8, TimeUnit.SECONDS)
			.callTimeout(10, TimeUnit.SECONDS)
			// Network interceptor that rewrites cache headers so OkHttp
			// will actually store responses for paths we want cached.
			// Many of our endpoints send Cache-Control: no-store (the
			// per-token /display/<token> page is the big one) which
			// otherwise prevents OkHttp from caching them at all. We
			// strip the no-store / no-cache directives and attach a
			// 30-day max-age so the disk cache fills up naturally.
			// This only runs for requests we already chose to route
			// through cacheFirst / networkFirst / staleWhileRevalidate.
			.addNetworkInterceptor { chain ->
				val resp = chain.proceed(chain.request())
				if (!shouldRewriteCacheHeaders(chain.request().url.toString())) {
					resp
				} else {
					resp.newBuilder()
						.removeHeader("Pragma")
						.removeHeader("Expires")
						.header("Cache-Control", "public, max-age=2592000, immutable")
						.build()
				}
			}
			.build()
		client = c
		return c
	}

	/** Long-running client for full media downloads. The normal WebView cache
	 * client has short call timeouts so offline fallback is fast, but videos
	 * can easily take longer than 10s to prefetch on Wi-Fi. */
	@Synchronized
	private fun mediaClient(): OkHttpClient {
		mediaDownloadClient?.let { return it }
		val c = LanHttps.apply(OkHttpClient.Builder())
			.connectTimeout(15, TimeUnit.SECONDS)
			.readTimeout(5, TimeUnit.MINUTES)
			.callTimeout(0, TimeUnit.MILLISECONDS)
			.build()
		mediaDownloadClient = c
		return c
	}

	/**
	 * Should we override the upstream Cache-Control for this URL? True for
	 * paths we route through one of our caching strategies â€” keeping the
	 * decision in one place so the network interceptor and the routing in
	 * intercept() never disagree.
	 */
	private fun shouldRewriteCacheHeaders(urlStr: String): Boolean {
		val serverUrl = Config.serverUrl
		if (serverUrl.isEmpty() || !ServerUrl.sameHost(urlStr, serverUrl)) return false
		val path = try { android.net.Uri.parse(urlStr).encodedPath } catch (_: Throwable) { null }
			?: return false
		if (path.endsWith("/events") || path.endsWith("/ping") || path.startsWith("/api/")) return false
		return path.startsWith("/plugin/")        ||
			   path.startsWith("/plugin_assets/") ||
			   path.startsWith("/static/")        ||
			   path.startsWith("/display/")       ||
			   (path.startsWith("/api/display/") && path.endsWith("/playlist"))
	}

	/**
	 * Routing decision for a single WebView resource request. Returns null
	 * if the request should fall through to the WebView's default loader
	 * (ranges, POSTs, SSE, etc.). The host is matched against the saved
	 * server URL so we never accidentally cache third-party requests like
	 * external plugin APIs (RainViewer, Open-Meteo, etc.) Ã¢â‚¬â€ those keep
	 * their own per-plugin caching strategies.
	 */
	fun intercept(ctx: Context, req: WebResourceRequest): WebResourceResponse? {
		// Only intercept simple GETs. POSTs etc go straight to the WebView's
		// own networking which handles them correctly out of the box.
		if (!req.method.equals("GET", ignoreCase = true)) return null

		val url = req.url ?: return null
		val urlStr = url.toString()

		// Only intercept requests to OUR server. Third-party APIs called
		// from inside plugins (weather, radar, etc.) need to keep their
		// own caching/fallback semantics.
		val serverUrl = Config.serverUrl
		if (serverUrl.isEmpty() || !ServerUrl.sameHost(urlStr, serverUrl)) return null

		val path = url.encodedPath ?: return null

		// Range header bypass. Range requests are typically <video>
		// streaming; we let those go to native WebView networking UNLESS
		// the URL is /uploads/... in which case our serveMedia handler
		// streams Range slices from disk so the video can play offline.
		// For everything else (HTML, JSON, JS, CSS) Range is meaningless
		// and we bypass to avoid any quirks.
		val rangeHeader = req.requestHeaders["Range"] ?: req.requestHeaders["range"]
		if (rangeHeader != null && !path.startsWith("/uploads/")) return null

		// Never cache live endpoints Ã¢â‚¬â€ these need real-time data.
		if (path.endsWith("/events") || path.endsWith("/ping")) {
			return null
		}

		// Playlist JSON — network-first with cache fallback (matches static/sw.js).
		if (path.matches(Regex("^/api/display/[^/]+/playlist$"))) {
			return networkFirst(ctx, urlStr)
		}

		if (path.startsWith("/api/")) {
			return null
		}

		return when {
			path.startsWith("/plugin/") || path.startsWith("/plugin_assets/") ->
				staleWhileRevalidate(ctx, urlStr)

			path == "/static/js/display_player.js" ->
				networkFirst(ctx, urlStr)

			// Static assets (JS/CSS/images) -- stale-while-revalidate so we
			// serve cached copy instantly but pick up updates next reload.
			// Previously cache-first, which meant the player could be stuck
			// running old display_player.js for the lifetime of the app
			// install -- no way to ship a JS bug fix without manually
			// clearing the OkHttp cache.
			path.startsWith("/static/") ->
				staleWhileRevalidate(ctx, urlStr)

			// Uploaded media (videos / images). Routed through serveMedia()
			// which handles Range requests properly: passthrough when the
			// server is reachable (so the WebView gets native streaming),
			// background-cache the full file for offline use, and on a
			// subsequent network failure slice the cached blob to satisfy
			// whatever Range header the WebView asked for. Mirrors the
			// browser SW's serveMedia() in static/sw.js.
			path.startsWith("/uploads/") ->
				serveMedia(ctx, req)

			path.startsWith("/display/") ->
				networkFirst(ctx, urlStr)

			else -> null
		}
	}

	// Ã¢â€â‚¬Ã¢â€â‚¬ Strategies Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

	/**
	 * cache-first: try cache, then network, then a stub 503.
	 * Used for /static/... and /uploads/... where the URL is content-addressed
	 * (immutable filename / UUID), so a cache hit is always correct.
	 */
	private fun cacheFirst(ctx: Context, url: String): WebResourceResponse {
		val cached = doRequest(ctx, url, onlyIfCached = true)
		if (cached != null && cached.isSuccessful) {
			FileLog.i(TAG, "cacheFirst CACHE HIT $url")
			return cached.toWebResponse()
		}
		cached?.close()
		val fresh = doRequest(ctx, url, onlyIfCached = false)
		if (fresh != null && fresh.isSuccessful) {
			FileLog.i(TAG, "cacheFirst NET HIT $url")
			return fresh.toWebResponse()
		}
		fresh?.close()
		FileLog.w(TAG, "cacheFirst MISS $url")
		return errorResponse()
	}

	/**
	 * network-first: try network, on failure fall back to cache, on second
	 * failure return a 503. Used for /display/<token> so config edits are
	 * picked up live but the player still boots from cache when offline.
	 */
	private fun networkFirst(ctx: Context, url: String): WebResourceResponse {
		val fresh = doRequest(ctx, url, onlyIfCached = false)
		if (fresh != null && fresh.isSuccessful) {
			FileLog.i(TAG, "networkFirst NET HIT $url")
			return fresh.toWebResponse()
		}
		fresh?.close()
		val cached = doRequest(ctx, url, onlyIfCached = true)
		if (cached != null && cached.isSuccessful) {
			FileLog.i(TAG, "networkFirst CACHE HIT $url")
			return cached.toWebResponse()
		}
		cached?.close()
		FileLog.w(TAG, "networkFirst MISS $url")
		return errorResponse()
	}

	/**
	 * stale-while-revalidate: serve cached copy immediately if any, then
	 * kick off a background fetch to refresh it for next time. Used for
	 * plugin HTML/assets which change infrequently but still need to pick
	 * up updates eventually.
	 *
	 * If there's no cached copy at all, fall through to network-first
	 * semantics so the first ever load still works.
	 */
	private fun staleWhileRevalidate(ctx: Context, url: String): WebResourceResponse {
		val cached = doRequest(ctx, url, onlyIfCached = true)
		if (cached != null && cached.isSuccessful) {
			// Read the bytes BEFORE we kick off the revalidation so we don't
			// close the cached body twice. Then trigger a background fetch
			// (we don't await it Ã¢â‚¬â€ it just warms the cache for next time).
			val resp = cached.toWebResponse()
			Thread {
				try { doRequest(ctx, url, onlyIfCached = false)?.close() } catch (_: Throwable) {}
			}.start()
			return resp
		}
		cached?.close()
		// No cache yet Ã¢â‚¬â€ fall back to network-first so first load works.
		return networkFirst(ctx, url)
	}

	/**
	 * Range-aware media handler for /uploads/... (videos and images).
	 *
	 *   1. When the network is reachable: passthrough to native WebView
	 *      networking (return null) so HTML5 <video> gets proper Range
	 *      streaming and seek. Kick off a background full-file download
	 *      into our media cache directory so we have offline coverage.
	 *   2. When the network fails AND we have the file cached on disk:
	 *      serve the requested byte range from the on-disk file. Returns
	 *      a 206 Partial Content response with the right Content-Range
	 *      headers so HTML5 <video> seeks normally.
	 *
	 * Cached files live in <filesDir>/media-cache/<sha1-of-url>.bin so
	 * they survive app upgrades (unlike OkHttp's cache directory). One
	 * file per uploaded asset; UUIDs in the URL mean they're immutable so
	 * we never need to revalidate.
	 */
	private fun serveMedia(ctx: Context, req: WebResourceRequest): WebResourceResponse? {
		val url = req.url.toString()
		val rangeHeader = req.requestHeaders["Range"] ?: req.requestHeaders["range"]
		val cacheFile   = mediaCacheFile(ctx, url)
		tryFinalizePartialCache(cacheFile)
		val hasFullCache = isMediaCacheComplete(ctx, url)

		// Uncached media must always reach the server via native WebView
		// networking. A flaky quickReachable() probe used to mark the server
		// "offline" and return 503 here, which left <video> on a black frame
		// mid-playback even when the server was up.
		val online = !forceOfflineMedia && quickReachable(ctx, url)

		if (!hasFullCache) {
			if (online) {
				ensureMediaPrefetch(url, cacheFile)
				return null   // passthrough — WebView streams while prefetch runs
			}
			// Offline: OkHttp may still have a full body from an earlier prefetch/browse.
			val okhttpCached = doRequest(ctx, url, onlyIfCached = true)
			if (okhttpCached != null && okhttpCached.isSuccessful) {
				FileLog.i(TAG, "serveMedia OFFLINE-OKHTTP $url")
				return try {
					okhttpCached.toWebResponse()
				} catch (e: Throwable) {
					okhttpCached.close()
					FileLog.w(TAG, "serveMedia OFFLINE-OKHTTP read failed $url: ${e.message}")
					errorResponse()
				}
			}
			okhttpCached?.close()
			if (!seedMediaCacheFromOkHttp(ctx, url, cacheFile)) {
				FileLog.w(TAG, "serveMedia OFFLINE-MISS (not cached) $url")
				return errorResponse()
			}
			FileLog.i(TAG, "serveMedia seeded offline from OkHttp cache $url")
		}

		// Serve the completed on-disk copy (Range-aware). Used when the server
		// is down or when we have a full local file (avoid passthrough to a
		// dead host while quickReachable TTL is still "online").
		if (online) ensureMediaPrefetch(url, cacheFile)

		val total = cacheFile.length()
		val mimeSidecar = File(cacheFile.parentFile, cacheFile.name + ".mime")
		val mimeFromDisk = try {
			if (mimeSidecar.exists() && mimeSidecar.length() > 0)
				mimeSidecar.readText(Charsets.UTF_8).trim().substringBefore(';')
			else null
		} catch (_: Throwable) { null }
		val mime  = mimeFromDisk?.takeIf { it.isNotEmpty() }
			?: sniffMediaMimeFromContent(cacheFile)
			?: guessMimeFromUrl(url)

		// No Range header -- serve the whole file as a 200 OK
		if (rangeHeader == null) {
			FileLog.i(TAG, "serveMedia OFFLINE-FULL $url ($total bytes)")
			val headers = mutableMapOf<String, String>(
				"Content-Length" to total.toString(),
				"Accept-Ranges"  to "bytes",
				"Cache-Control"  to "no-store"
			)
			val resp = WebResourceResponse(mime, null, java.io.FileInputStream(cacheFile))
			resp.setStatusCodeAndReasonPhrase(200, "OK")
			resp.responseHeaders = headers
			return resp
		}

		// Range request -- parse "bytes=START-END" and slice
		val m = Regex("""bytes=(\d+)-(\d*)""").find(rangeHeader)
		val start = m?.groupValues?.get(1)?.toLongOrNull() ?: 0L
		val end   = m?.groupValues?.get(2)?.toLongOrNull()?.takeIf { it > 0 } ?: (total - 1)
		val safeStart = start.coerceAtLeast(0L).coerceAtMost(total - 1)
		val safeEnd   = end.coerceAtLeast(safeStart).coerceAtMost(total - 1)
		val length    = safeEnd - safeStart + 1

		FileLog.i(TAG, "serveMedia OFFLINE-RANGE $url bytes=$safeStart-$safeEnd/$total")

		val ras   = java.io.RandomAccessFile(cacheFile, "r")
		ras.seek(safeStart)
		val bounded = BoundedFileInputStream(ras, length)

		val headers = mutableMapOf<String, String>(
			"Content-Length" to length.toString(),
			"Content-Range"  to "bytes $safeStart-$safeEnd/$total",
			"Accept-Ranges"  to "bytes",
			"Cache-Control"  to "no-store"
		)
		val resp = WebResourceResponse(mime, null, bounded)
		resp.setStatusCodeAndReasonPhrase(206, "Partial Content")
		resp.responseHeaders = headers
		return resp
	}

	/**
	 * Cheap reachability probe with a tiny in-process cache. The result
	 * is reused for 3 seconds so a video that issues many sequential
	 * Range requests doesn't pay the TCP-connect cost on every chunk.
	 * 3s is short enough that we recover quickly when the server comes
	 * back, long enough to amortize during normal playback.
	 */
	@Volatile private var lastReachableUrl  = ""
	@Volatile private var lastReachableTs   = 0L
	@Volatile private var lastReachableVal  = false
	/** When true, never passthrough /uploads/ to the network (app is down, proxy may still accept TCP). */
	@Volatile private var forceOfflineMedia = false

	/** Called when SSE/ping confirms the server is up — avoids stale "offline" probes. */
	fun markServerReachable(serverUrl: String) {
		val key = serverReachabilityKey(serverUrl)
		forceOfflineMedia = false
		lastReachableUrl = key
		lastReachableVal = true
		lastReachableTs  = System.currentTimeMillis()
	}

	/** Called when ping/SSE fail — stop passthrough streaming to a dead server. */
	fun markServerUnreachable(serverUrl: String) {
		val key = serverReachabilityKey(serverUrl)
		forceOfflineMedia = true
		lastReachableUrl = key
		lastReachableVal = false
		lastReachableTs  = System.currentTimeMillis()
	}

	private fun serverReachabilityKey(serverUrl: String): String {
		return try {
			val u = java.net.URL(serverUrl.trimEnd('/') + "/")
			u.protocol + "://" + u.host + ":" + (if (u.port == -1) {
				if (u.protocol == "https") 443 else 80
			} else u.port)
		} catch (_: Throwable) { serverUrl }
	}

	private fun quickReachable(ctx: Context, url: String): Boolean {
		val key = try {
			val u = java.net.URL(url)
			u.protocol + "://" + u.host + ":" + (if (u.port == -1) {
				if (u.protocol == "https") 443 else 80
			} else u.port)
		} catch (_: Throwable) { url }
		val now = System.currentTimeMillis()
		// Cache probe result briefly. Use a longer TTL when the server was
		// reachable so a single slow /healthz during video playback does not
		// flip every Range request to "offline" for three seconds.
		if (forceOfflineMedia) return false
		val ttl = if (lastReachableVal) 15_000L else 3_000L
		if (key == lastReachableUrl && now - lastReachableTs < ttl) {
			return lastReachableVal
		}
		val ok = try {
			val u = java.net.URL(url)
			val base = "${u.protocol}://${u.host}" + if (u.port == -1) "" else ":${u.port}"
			val req = Request.Builder().url("$base/api/version").get().build()
			client(ctx).newCall(req).execute().use { resp -> resp.isSuccessful }
		} catch (_: Throwable) {
			false
		}
		lastReachableUrl = key
		lastReachableTs  = now
		lastReachableVal = ok
		return ok
	}

	/** Light reachability probe for native offline UI (uses /api/version). */
	fun quickReachableForServer(ctx: Context, serverUrl: String): Boolean {
		val base = serverUrl.trimEnd('/')
		return quickReachable(ctx, "$base/api/version")
	}

	/** <filesDir>/media-cache/<sha1>.bin -- survives upgrades. */
	private fun mediaCanonicalUrl(url: String): String {
		return try {
			val u = java.net.URL(url)
			val path = u.path ?: return url
			// /uploads/ UUIDs are immutable — key by path so http:5000 and
			// https:443 on the same host share one on-disk copy.
			if (path.startsWith("/uploads/")) return path
			"${u.protocol}://${u.host}$path"
		} catch (_: Throwable) {
			url
		}
	}

	private fun mediaCacheFile(ctx: Context, url: String): File {
		val dir = File(ctx.filesDir, "media-cache").apply { mkdirs() }
		val md = java.security.MessageDigest.getInstance("SHA-1")
		val hex = md.digest(mediaCanonicalUrl(url).toByteArray(Charsets.UTF_8))
			.joinToString("") { "%02x".format(it) }
		return File(dir, "$hex.bin")
	}

	/**
	 * Warm media, plugin, and player-page caches from the playlist payload.
	 * Called from the in-page player via AISignXNative.prefetchPlaylist().
	 */
	/** Commit a .part file when it is at least 95% of the expected Content-Length. */
	private fun tryFinalizePartialCache(dest: File) {
		val part = File(dest.parentFile, dest.name + ".part")
		if (!part.exists() || part.length() <= 0L) return
		val lenSide = File(dest.parentFile, dest.name + ".len")
		if (!lenSide.exists()) return
		val expected = try {
			lenSide.readText(Charsets.UTF_8).trim().toLongOrNull()
		} catch (_: Throwable) {
			null
		} ?: return
		if (part.length() < (expected * 95L / 100L)) return
		try {
			if (dest.exists()) dest.delete()
			if (!part.renameTo(dest)) {
				part.copyTo(dest, overwrite = true)
				part.delete()
			}
			FileLog.i(TAG, "media-prefetch finalized partial ${dest.length()}/$expected bytes")
		} catch (e: Throwable) {
			FileLog.w(TAG, "media-prefetch finalize failed: ${e.message}")
		}
	}

	/** True when the full media file was committed (not a partial .part download). */
	fun isMediaCacheComplete(ctx: Context, url: String): Boolean {
		if (url.isBlank()) return false
		val f = mediaCacheFile(ctx, url)
		tryFinalizePartialCache(f)
		if (!f.exists() || f.length() <= 0L) return false
		val part = File(f.parentFile, f.name + ".part")
		if (part.exists()) return false
		val lenSide = File(f.parentFile, f.name + ".len")
		if (lenSide.exists()) {
			try {
				val expected = lenSide.readText(Charsets.UTF_8).trim().toLongOrNull() ?: return true
				return f.length() == expected
			} catch (_: Throwable) {
				return true
			}
		}
		return true
	}

	fun isMediaCached(ctx: Context, url: String): Boolean = isMediaCacheComplete(ctx, url)

	/** Whether OkHttp disk cache has a successful GET for this exact URL. */
	fun hasCachedResource(ctx: Context, url: String): Boolean {
		if (url.isBlank()) return false
		return try {
			val resp = doRequest(ctx, url, onlyIfCached = true)
			val ok = resp != null && resp.isSuccessful
			resp?.close()
			ok
		} catch (_: Throwable) {
			false
		}
	}

	fun requestMediaPrefetch(url: String, ctx: Context) {
		if (url.isBlank()) return
		val dest = mediaCacheFile(ctx.applicationContext, url)
		if (!dest.exists() || dest.length() <= 0L) {
			ensureMediaPrefetch(url, dest)
		}
	}

	fun prefetchPlaylist(
		ctx: Context,
		mediaUrls: List<String>,
		pluginUrls: List<String>,
		pageUrls: List<String>
	) {
		val appCtx = ctx.applicationContext
		val videos = mutableListOf<String>()
		val otherMedia = mutableListOf<String>()
		for (url in mediaUrls) {
			if (url.isBlank()) continue
			val lower = url.lowercase()
			if (lower.contains(".mp4") || lower.contains(".webm") || lower.contains(".mov") ||
				lower.contains(".m4v") || lower.contains("/videos/")) {
				videos.add(url)
			} else {
				otherMedia.add(url)
			}
		}
		val orderedMedia = videos + otherMedia
		Thread {
			prefetchMediaList(appCtx, orderedMedia)
		}.start()
		Thread {
			for (url in pluginUrls) {
				if (url.isBlank()) continue
				prefetchPluginBundle(appCtx, url)
			}
			for (url in pageUrls) {
				if (url.isBlank()) continue
				try { doRequest(appCtx, url, onlyIfCached = false)?.close() } catch (_: Throwable) {}
			}
		}.start()
	}

	private fun prefetchMediaList(ctx: Context, urls: List<String>) {
		if (urls.isEmpty()) return
		val lock = Any()
		var idx = 0
		repeat(PREFETCH_CONCURRENCY.coerceAtMost(urls.size)) {
			Thread {
				while (true) {
					val i = synchronized(lock) {
						if (idx >= urls.size) -1 else idx++
					}
					if (i < 0) return@Thread
					val url = urls[i]
					val dest = mediaCacheFile(ctx, url)
					if (!dest.exists() || dest.length() <= 0L) {
						ensureMediaPrefetch(url, dest)
					}
				}
			}.start()
		}
	}

	/** Fetch plugin HTML and warm same-origin /plugin_assets/ + /static/ refs. */
	private fun prefetchPluginBundle(ctx: Context, pluginUrl: String) {
		try {
			val resp = doRequest(ctx, pluginUrl, onlyIfCached = false) ?: return
			if (!resp.isSuccessful) {
				resp.close()
				return
			}
			val html = resp.body?.string() ?: ""
			resp.close()
			val base = Config.serverUrl
			val urls = linkedSetOf(pluginUrl)
			val re = Regex("""(?:src|href)\s*=\s*["']([^"']+)["']""", RegexOption.IGNORE_CASE)
			for (m in re.findAll(html)) {
				var ref = m.groupValues[1].trim()
				if (ref.isEmpty() || ref.startsWith("data:") || ref.startsWith("javascript:")) continue
				val abs = when {
					ref.startsWith("http://") || ref.startsWith("https://") -> ref
					ref.startsWith("//") -> "https:$ref"
					ref.startsWith("/") -> "$base$ref"
					else -> continue
				}
				if (ServerUrl.sameHost(abs, base) &&
					(abs.contains("/plugin_assets/") || abs.contains("/static/"))) {
					urls.add(abs)
				}
			}
			for (u in urls) {
				try { doRequest(ctx, u, onlyIfCached = false)?.close() } catch (_: Throwable) {}
			}
			FileLog.i(TAG, "plugin-prefetch warmed ${urls.size} URL(s) for $pluginUrl")
		} catch (e: Throwable) {
			FileLog.w(TAG, "plugin-prefetch failed $pluginUrl: ${e.message}")
		}
	}

	/** Copy a full cached HTTP body into media-cache for offline Range serving. */
	private fun seedMediaCacheFromOkHttp(ctx: Context, url: String, dest: File): Boolean {
		val resp = doRequest(ctx, url, onlyIfCached = true) ?: return false
		if (!resp.isSuccessful) {
			resp.close()
			return false
		}
		return try {
			val bytes = resp.body?.bytes() ?: ByteArray(0)
			if (bytes.isEmpty()) return false
			dest.parentFile?.mkdirs()
			val tmp = File(dest.parentFile, dest.name + ".part")
			tmp.writeBytes(bytes)
			if (dest.exists()) dest.delete()
			if (!tmp.renameTo(dest)) {
				tmp.copyTo(dest, overwrite = true)
				tmp.delete()
			}
			val ct = resp.header("Content-Type")?.substringBefore(';')?.trim()
			if (!ct.isNullOrEmpty()) {
				File(dest.parentFile, dest.name + ".mime").writeText(ct, Charsets.UTF_8)
			}
		 true
		} catch (_: Throwable) {
			false
		} finally {
			resp.close()
		}
	}

	private fun guessMimeFromUrl(url: String): String {
		val lower = url.lowercase()
		return when {
			lower.endsWith(".mp4")  -> "video/mp4"
			lower.endsWith(".webm") -> "video/webm"
			lower.endsWith(".mov")  -> "video/quicktime"
			lower.endsWith(".m4v")  -> "video/mp4"
			lower.endsWith(".jpg") || lower.endsWith(".jpeg") -> "image/jpeg"
			lower.endsWith(".png")  -> "image/png"
			lower.endsWith(".gif")  -> "image/gif"
			lower.endsWith(".webp") -> "image/webp"
			else                    -> "application/octet-stream"
		}
	}

	/** When URL has no extension and no .mime sidecar, detect common container magic. */
	private fun sniffMediaMimeFromContent(file: File): String? {
		if (!file.exists() || file.length() < 12L) return null
		return try {
			java.io.RandomAccessFile(file, "r").use { raf ->
				val buf = ByteArray(12)
				raf.readFully(buf)
				// MP4 / ISO BMFF: size(4) + "ftyp" at offset 4
				if (buf[4] == 'f'.code.toByte() && buf[5] == 't'.code.toByte() &&
					buf[6] == 'y'.code.toByte() && buf[7] == 'p'.code.toByte()) {
					return "video/mp4"
				}
				// WebM / Matroska EBML id 0x1A45DFA3
				if (buf[0] == 0x1a.toByte() && buf[1] == 0x45.toByte() &&
					buf[2] == 0xdf.toByte() && buf[3] == 0xa3.toByte()) {
					return "video/webm"
				}
				null
			}
		} catch (_: Throwable) {
			null
		}
	}

	/**
	 * Background-download a media file into mediaCacheFile so that the
	 * NEXT time we go offline we can serve it. Throttled by a global set
	 * of in-flight URLs to avoid two simultaneous downloads of the same
	 * file when several tabs/iframes request it at once.
	 */
	@Volatile private var prefetchInFlight = mutableSetOf<String>()

	private fun ensureMediaPrefetch(url: String, dest: File) {
		synchronized(prefetchInFlight) {
			if (!prefetchInFlight.add(url)) return  // already downloading
		}
		Thread {
			val tmp = File(dest.parentFile, dest.name + ".part")
			try {
				var lastError: Throwable? = null
				for (attempt in 1..3) {
					try {
						if (tmp.exists()) tmp.delete()
						val req = Request.Builder()
							.url(url)
							.header("Cache-Control", "no-cache")
							.build()
						mediaClient().newCall(req).execute().use { resp ->
							if (!resp.isSuccessful) {
								throw java.io.IOException("HTTP ${resp.code}")
							}
							val expected = resp.header("Content-Length")?.toLongOrNull() ?: -1L
							val ct = resp.header("Content-Type")?.substringBefore(';')?.trim()
							resp.body?.byteStream()?.use { input ->
								java.io.FileOutputStream(tmp).use { out ->
									input.copyTo(out, 256 * 1024)
								}
							}
							if (tmp.length() <= 0L) {
								throw java.io.IOException("empty response")
							}
							if (expected > 0L && tmp.length() != expected) {
								throw java.io.IOException("incomplete download ${tmp.length()}/$expected bytes")
							}
							if (dest.exists()) dest.delete()
							if (!tmp.renameTo(dest)) {
								throw java.io.IOException("failed to commit cache file")
							}
							try {
								if (!ct.isNullOrEmpty()) {
									val side = File(dest.parentFile, dest.name + ".mime")
									side.writeText(ct, Charsets.UTF_8)
								}
								if (expected > 0L) {
									val lenSide = File(dest.parentFile, dest.name + ".len")
									lenSide.writeText(expected.toString(), Charsets.UTF_8)
								}
							} catch (_: Throwable) {}
							FileLog.i(TAG, "media-prefetch OK ${dest.length()} bytes $url")
							return@Thread
						}
					} catch (e: Throwable) {
						lastError = e
						FileLog.w(TAG, "media-prefetch attempt $attempt failed $url: ${e.message}")
						try { if (tmp.exists()) tmp.delete() } catch (_: Throwable) {}
						if (attempt < 3) Thread.sleep((attempt * 1500L).coerceAtMost(5000L))
					}
				}
				FileLog.w(TAG, "media-prefetch FAIL $url: ${lastError?.message}")
			} catch (e: Throwable) {
				FileLog.w(TAG, "media-prefetch FAIL $url: ${e.message}")
			} finally {
				try { if (tmp.exists()) tmp.delete() } catch (_: Throwable) {}
				synchronized(prefetchInFlight) { prefetchInFlight.remove(url) }
			}
		}.start()
	}

	// HTTP plumbing

	private fun doRequest(ctx: Context, url: String, onlyIfCached: Boolean): Response? {
		return try {
			val cc = if (onlyIfCached) {
				CacheControl.Builder().onlyIfCached().maxStale(365, TimeUnit.DAYS).build()
			} else {
				CacheControl.Builder().maxAge(0, TimeUnit.SECONDS).build()
			}
			val req = Request.Builder()
				.url(url)
				.cacheControl(cc)
				.build()
			client(ctx).newCall(req).execute()
		} catch (_: Throwable) {
			null
		}
	}

	private fun errorResponse(): WebResourceResponse {
		// Non-2xx WebResourceResponse so the page treats it as a load failure
		// (matching the SW behaviour in static/sw.js).
		val r = WebResourceResponse(
			"text/plain",
			"utf-8",
			ByteArrayInputStream(ByteArray(0))
		)
		r.setStatusCodeAndReasonPhrase(503, "Service Unavailable")
		return r
	}

	private fun Response.toWebResponse(): WebResourceResponse {
		val mimeType = this.header("Content-Type")?.substringBefore(';')?.trim()
			?: "application/octet-stream"
		// Some sites send 'charset' as a separate parameter; pluck it off.
		val charset = this.header("Content-Type")
			?.substringAfter("charset=", "")
			?.takeIf { it.isNotEmpty() }
			?: "utf-8"
		// Read full body bytes -- we have to buffer because the WebView
		// expects a re-readable InputStream.
		val bytes = this.body?.bytes() ?: ByteArray(0)
		val responseHeaders = mutableMapOf<String, String>()
		for ((name, value) in this.headers) {
			// Strip headers that confuse the WebView when we proxy them
			// (chunked/length issues, CORS, etc.).
			val lower = name.lowercase()
			if (lower in INTERNAL_HEADERS) continue
			responseHeaders[name] = value
		}
		responseHeaders["Content-Length"] = bytes.size.toString()
		val resp = WebResourceResponse(
			mimeType,
			charset,
			ByteArrayInputStream(bytes)
		)
		resp.setStatusCodeAndReasonPhrase(this.code,
			this.message.ifEmpty { if (this.isSuccessful) "OK" else "Error" })
		resp.responseHeaders = responseHeaders
		this.close()
		return resp
	}

	private val INTERNAL_HEADERS = setOf(
		"transfer-encoding",
		"content-encoding",
		"content-length",
		"connection",
		"keep-alive"
	)
}
