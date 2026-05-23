package com.aisignx.player

import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import okhttp3.*
import java.util.concurrent.TimeUnit

/**
 * Connects to /display/<token>/events (Server-Sent Events) and emits each
 * named event as an [SseEvent]. Reconnects automatically on any failure
 * with exponential backoff (1s, 2s, 4s, ... capped at 30s).
 *
 * Use [start] / [stop] from Activity lifecycle. Subscribe to [events] from
 * anywhere — typically `lifecycleScope.launch { sse.events.collect { ... } }`.
 */
object SseClient {

	private const val TAG = "AISignX/SSE"

	private val sseHttp = OkHttpClient.Builder()
		.connectTimeout(15, TimeUnit.SECONDS)
		.readTimeout(0, TimeUnit.MILLISECONDS)        // SSE = no read timeout
		.pingInterval(30, TimeUnit.SECONDS)
		.hostnameVerifier { _, _ -> true }
		.build()

	private val _events = MutableSharedFlow<SseEvent>(extraBufferCapacity = 16)
	val events: SharedFlow<SseEvent> = _events.asSharedFlow()

	/**
	 * Tracks whether we currently have an open SSE stream to the server.
	 * Flips to true on a successful HTTP 200 response, and back to false
	 * the moment the stream errors, ends, or stop() is called. Subscribers
	 * (e.g. PlayerActivity) use this to detect a "server came back online"
	 * transition and trigger a WebView reload.
	 */
	private val _connected = MutableStateFlow(false)
	val connected: StateFlow<Boolean> = _connected.asStateFlow()

	private var loopJob: Job? = null
	private var currentCall: Call? = null

	fun start(scope: CoroutineScope, serverUrl: String, token: String, clientId: String = "") {
		stop()
		loopJob = scope.launch(Dispatchers.IO) {
			var backoffMs = 1_000L
			while (isActive) {
				try {
					Log.i(TAG, "Connecting SSE: $serverUrl/display/$token/events")
					streamOnce(serverUrl, token, clientId)
					Log.i(TAG, "SSE stream ended cleanly")
					backoffMs = 1_000L
				} catch (e: CancellationException) {
					throw e
				} catch (e: Exception) {
					Log.w(TAG, "SSE error: ${e.message} — reconnecting in ${backoffMs}ms")
				}
				_connected.value = false
				delay(backoffMs)
				backoffMs = (backoffMs * 2).coerceAtMost(30_000L)
			}
		}
	}

	fun stop() {
		_connected.value = false
		loopJob?.cancel()
		loopJob = null
		try { currentCall?.cancel() } catch (_: Exception) {}
		currentCall = null
	}

	private suspend fun streamOnce(serverUrl: String, token: String, clientId: String) {
		val suffix = if (clientId.isNotBlank()) "?client_id=${java.net.URLEncoder.encode(clientId, "UTF-8")}" else ""
		val req = Request.Builder()
			.url("$serverUrl/display/$token/events$suffix")
			.header("Accept", "text/event-stream")
			.header("Cache-Control", "no-cache")
			.build()
		val call = sseHttp.newCall(req)
		currentCall = call
		call.execute().use { resp ->
			if (!resp.isSuccessful) throw RuntimeException("HTTP ${resp.code}")
			_connected.value = true
			val source = resp.body?.source() ?: throw RuntimeException("No body")
			var event = "message"
			val data = StringBuilder()
			while (!source.exhausted()) {
				val line = source.readUtf8Line() ?: break
				when {
					line.isEmpty() -> {
						// Dispatch the buffered event
						if (data.isNotEmpty()) {
							val payload = data.toString().trim()
							Log.d(TAG, "event=$event data=$payload")
							_events.tryEmit(SseEvent(event, payload))
						}
						event = "message"
						data.clear()
					}
					line.startsWith("event:") -> event = line.removePrefix("event:").trim()
					line.startsWith("data:")  -> {
						if (data.isNotEmpty()) data.append('\n')
						data.append(line.removePrefix("data:").trim())
					}
					line.startsWith(":")      -> { /* SSE comment / heartbeat */ }
				}
			}
		}
	}

	data class SseEvent(val type: String, val data: String)
}
