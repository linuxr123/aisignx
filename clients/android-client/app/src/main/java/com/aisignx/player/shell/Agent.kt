package com.aisignx.player.shell

import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import com.aisignx.player.AiSignXApp
import com.aisignx.player.BuildConfig
import com.aisignx.player.FileLog
import com.aisignx.player.net.Http
import com.aisignx.player.ui.LockTask
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * The device agent inside the player app (docs/device-agent-design.md): the hang watchdog, the
 * command poll and the capability report. Owned by PlayerActivity; lives while it lives.
 *
 * Commands come only from the app's own authenticated, pinned HTTPS connection, never through
 * the page. The display token is the HttpOnly cookie `aisignx_display`: the WebView's cookie
 * store returns it to native code, and it is sent as `Authorization: Bearer`. It is kept in a
 * local variable for one request and never stored or logged.
 *
 * The verbs are a closed list (restart_player, collect_logs); nothing taken from the server is
 * ever executed, used as a path or opened as a URL. Rules live in AgentRules.kt.
 */
class Agent(
    context: Context,
    private val host: Host,
) {
    /** Main-thread actions of the activity. */
    interface Host {
        /** The player page (not the offline page) is what the WebView shows. */
        fun pageHealthy(): Boolean
        fun reloadPage()
        fun recreateWebView()
        fun recreateActivity()
    }

    private val ctx = context.applicationContext
    private val app = AiSignXApp.from(ctx)
    private val store = AgentStore(ctx)
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var settings: AgentSettings = store.settings
    private var executor: ScheduledExecutorService? = null

    // Watchdog state, main thread only (monotonic clock).
    private var resumed = false
    private var lastAlive = SystemClock.elapsedRealtime()
    private var pageLoaded = lastAlive
    private var doneLevel = 0

    private val tick = object : Runnable {
        override fun run() {
            watchdogTick()
            main.postDelayed(this, TICK_MS)
        }
    }

    // ---- lifecycle (main thread) ----------------------------------------------------

    fun start() {
        if (executor != null) return
        main.postDelayed(tick, TICK_MS)
        executor = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "aisignx-agent").apply { isDaemon = true } }
            .also { it.scheduleWithFixedDelay({ pollSafely() }, FIRST_POLL_S, POLL_S, TimeUnit.SECONDS) }
    }

    fun stop() {
        main.removeCallbacks(tick)
        executor?.shutdownNow()
        executor = null
    }

    fun onResume() { resumed = true; resetClock() }
    fun onPause() { resumed = false }

    /** The player page finished loading: judge it only after the grace period. */
    fun onPageLoaded() { pageLoaded = SystemClock.elapsedRealtime() }

    /** {"t":"alive"} from the page. */
    fun onAlive() { lastAlive = SystemClock.elapsedRealtime(); doneLevel = 0 }

    /** Worker thread: the manifest's switches. */
    fun onManifest(json: String) {
        val s = AgentSettings.parse(json) ?: return
        if (s != settings) {
            FileLog.i(TAG, "agent switches: watchdog=${s.watchdog} restart=${s.restart} logs=${s.logs}")
            settings = s
            store.settings = s
        }
    }

    private fun resetClock() { lastAlive = SystemClock.elapsedRealtime(); doneLevel = 0 }

    // ---- watchdog -------------------------------------------------------------------

    private fun watchdogTick() {
        val now = SystemClock.elapsedRealtime()
        val screenOn = (ctx.getSystemService(Context.POWER_SERVICE) as? PowerManager)?.isInteractive != false
        // Paused, screen off, switched off or showing the offline page: time does not count.
        if (!resumed || !screenOn || !settings.watchdog || !host.pageHealthy()) { resetClock(); return }
        val wall = System.currentTimeMillis()
        val d = Watchdog.decide(now, lastAlive, pageLoaded, doneLevel, AgentRates.recent(store.heavyTimes(), wall))
        if (d.level == 0) return
        doneLevel = d.level
        if (d.gaveUp) {
            store.gaveUpMs = wall
            FileLog.w(TAG, "watchdog: no alive for ${(now - lastAlive) / 1000}s but the hourly limit for restarts is used up; giving up")
            return
        }
        FileLog.w(TAG, "watchdog: no alive for ${(now - lastAlive) / 1000}s -> ${d.step}")
        when (d.step) {
            Watchdog.Step.RELOAD_PAGE -> { pageLoaded = now; host.reloadPage() }
            Watchdog.Step.RECREATE_WEBVIEW -> { pageLoaded = now; host.recreateWebView() }
            Watchdog.Step.RECREATE_ACTIVITY -> { store.addHeavy(wall); store.addRestart(wall); host.recreateActivity() }
            Watchdog.Step.RESTART_PROCESS -> { store.addHeavy(wall); store.addRestart(wall); AgentRestart.restartProcess(ctx, "player page hung") }
            Watchdog.Step.NONE -> Unit
        }
    }

    // ---- capability report (any thread) ---------------------------------------------

    /** stats().agent. No secrets. */
    fun report(): JSONObject {
        val wall = System.currentTimeMillis()
        val gaveUp = store.gaveUpMs.let { it > 0L && wall - it < AgentRates.HOUR_MS }
        return JSONObject()
            .put("version", BuildConfig.VERSION_NAME)
            .put("tier", if (LockTask.isDeviceOwner(ctx)) "device_owner" else "plain")
            .put("can_restart", true)
            .put("can_collect_logs", true)
            .put("can_reboot", false)
            .put("can_update", false)
            .put("watchdog", settings.watchdog)
            .put("restarts_1h", AgentRates.recent(store.restartTimes(), wall))
            .put("gave_up", gaveUp)
            .put("last_restart_ms", store.lastRestartMs)
    }

    // ---- command poll (agent thread) ------------------------------------------------

    private fun pollSafely() {
        try {
            if (settings.polls) poll()
        } catch (e: Exception) {
            FileLog.w(TAG, "command poll failed: ${e.javaClass.simpleName}")
        }
    }

    private fun poll() {
        val origin = app.config.serverUrl ?: return
        val base = "$origin/api/player/v1"
        val token = LogPrep.tokenFromCookies(Http.cookieFor("$base/commands")) ?: return // not paired yet
        val req = Request.Builder().url("$base/commands").header("Accept", "application/json")
            .header("Authorization", "Bearer $token").build()
        val body = http().newCall(req).execute().use { r ->
            if (!r.isSuccessful) {
                FileLog.w(TAG, "GET /commands: HTTP ${r.code}")
                return
            }
            r.peekBody(MAX_BODY).string()
        }
        for (c in AgentCommands.parseList(body)) {
            when (val v = AgentCommands.check(c, settings, store.handledSet())) {
                is AgentCommands.Verdict.Ignore -> Unit
                is AgentCommands.Verdict.Refuse -> {
                    store.markHandled(v.id)
                    FileLog.w(TAG, "command ${v.id} refused: ${v.result}")
                    ack(base, token, v.id, "failed", v.result)
                }
                is AgentCommands.Verdict.Run -> {
                    store.markHandled(v.id) // before acting: at most once
                    run(base, token, v)
                }
            }
        }
    }

    private fun run(base: String, token: String, v: AgentCommands.Verdict.Run) {
        FileLog.i(TAG, "command ${v.id}: ${v.verb}")
        when (v.verb) {
            AgentCommands.RESTART -> {
                ack(base, token, v.id, "done", "restarting")
                main.post { restartPlayer() }
            }
            AgentCommands.LOGS -> {
                val ok = try { uploadLogs(base, token, v.id) } catch (e: Exception) {
                    FileLog.w(TAG, "log upload failed: ${e.javaClass.simpleName}")
                    false
                }
                if (ok) ack(base, token, v.id, "done", "uploaded") else ack(base, token, v.id, "failed", "upload failed")
            }
        }
    }

    /**
     * Recreates the activity (always possible). The process is restarted only where the relaunch
     * is reliable (Device Owner or Home mode); on a plain device killing the process could leave
     * a dark screen, because Android 10+ may refuse to start the app from the background.
     */
    private fun restartPlayer() {
        val wall = System.currentTimeMillis()
        store.addRestart(wall)
        if (AgentRestart.relaunchIsReliable(ctx)) AgentRestart.restartProcess(ctx, "restart_player command")
        else host.recreateActivity()
    }

    private fun uploadLogs(base: String, token: String, id: String): Boolean {
        val head = buildString {
            append("AiSignX agent log collection\n")
            append("version=${BuildConfig.VERSION_NAME} build=${BuildConfig.VERSION_CODE}\n")
            append("device=${ShellInfo.deviceName(Build.MANUFACTURER, Build.MODEL, Build.VERSION.RELEASE)} sdk=${Build.VERSION.SDK_INT}\n")
            append("tier=${if (LockTask.isDeviceOwner(ctx)) "device_owner" else "plain"}\n---\n")
        }
        val raw = head + FileLog.files().joinToString("") { f -> tail(f) }
        val clean = LogPrep.redact(raw, listOfNotNull(token, app.config.certPin))
        val gz = LogPrep.gzipNewest(clean)
        val req = Request.Builder().url("$base/commands/$id/logs").header("Authorization", "Bearer $token")
            .post(gz.toRequestBody("application/gzip".toMediaType())).build()
        return http().newCall(req).execute().use { it.isSuccessful.also { ok -> if (!ok) FileLog.w(TAG, "POST logs: HTTP ${it.code}") } }
    }

    private fun tail(f: java.io.File): String = try {
        val b = f.readBytes()
        String(b, maxOf(0, b.size - MAX_LOG_READ), minOf(b.size, MAX_LOG_READ), Charsets.UTF_8)
    } catch (_: Exception) { "" }

    private fun ack(base: String, token: String, id: String, state: String, result: String): Boolean = try {
        val json = JSONObject().put("state", state).put("result", result.take(200)).toString()
        val req = Request.Builder().url("$base/commands/$id/ack").header("Authorization", "Bearer $token")
            .post(json.toRequestBody("application/json".toMediaType())).build()
        http().newCall(req).execute().use { r ->
            if (!r.isSuccessful) FileLog.w(TAG, "ack $id: HTTP ${r.code}")
            r.isSuccessful
        }
    } catch (e: Exception) {
        FileLog.w(TAG, "ack $id failed: ${e.javaClass.simpleName}")
        false
    }

    /** The app's pinned client (certificate checks unchanged), but never following a redirect with the token. */
    private fun http(): OkHttpClient = Http.client(app.config.certPin).newBuilder()
        .followRedirects(false).writeTimeout(30, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()

    companion object {
        private const val TAG = "Agent"
        private const val TICK_MS = 5_000L
        private const val POLL_S = 20L
        private const val FIRST_POLL_S = 10L
        private const val MAX_BODY = 64L * 1024
        private const val MAX_LOG_READ = 2 * 1024 * 1024
    }
}
