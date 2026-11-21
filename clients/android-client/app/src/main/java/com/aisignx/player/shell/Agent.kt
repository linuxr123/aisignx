package com.aisignx.player.shell

import android.app.admin.DevicePolicyManager
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
import com.aisignx.player.ui.HomeMode
import com.aisignx.player.ui.LockTask
import com.aisignx.player.ui.LockdownApplier
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
    private var lastKeyCheck = 0L

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
        displayIdOf(json)?.let { store.displayId = it }
        val s = AgentSettings.parse(json) ?: return
        if (s != settings) {
            FileLog.i(TAG, "agent switches: watchdog=${s.watchdog} restart=${s.restart} logs=${s.logs} " +
                "reboot=${s.reboot} startup=${s.startup} update=${s.update}")
            settings = s
            store.settings = s
        }
    }

    /** manifest.display.id: the signed commands name the display they are for. */
    private fun displayIdOf(json: String): Long? = try {
        SignedCommand.integer(JSONObject(json).optJSONObject("display")?.opt("id"))?.takeIf { it > 0 }
    } catch (_: Exception) { null }

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
            .put("can_reboot", caps().canReboot)
            .put("can_update", caps().canUpdate)
            .put("can_set_startup", caps().canSetStartup)
            .put("startup", JSONObject().put("autostart", Installer.autostart(ctx))
                .put("mode", StartupRules.reportedMode(app.config.homeMode, LockdownApplier.engaged(ctx))).put("shell_capable", true))
            .put("watchdog", settings.watchdog)
            .put("restarts_1h", AgentRates.recent(store.restartTimes(), wall))
            .put("gave_up", gaveUp)
            .put("last_restart_ms", store.lastRestartMs)
            .also { r ->
                TierRules.showUpdateError(store.updateError, BuildConfig.VERSION_NAME, wall)?.let { r.put("last_update_error", it) }
                val origin = app.config.serverUrl
                if (origin != null && store.keyChanged(origin)) r.put("command_key_changed", true)
            }
    }

    /** What this device can do for signed commands: reboot only as Device Owner; updates and autostart always. */
    private fun caps() = SignedCommand.Caps(
        canReboot = LockTask.isDeviceOwner(ctx),
        canUpdate = true,
        canSetStartup = true,
    )

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
        // server_time_ms is unsigned: keep a per-server high-water mark and distrust a response that goes backwards.
        val rawTime = AgentCommands.serverTimeMs(body)
        val timeOk = rawTime == null || store.noteServerTime(origin, rawTime)
        if (!timeOk) FileLog.w(TAG, "server_time_ms went backwards: signed commands are not trusted in this response")
        val serverTime = if (timeOk) rawTime else null
        // Pin the command key early (trust on first use) and look for a change now and then.
        val tierB = settings.reboot || settings.startup || settings.update
        val nowMs = SystemClock.elapsedRealtime()
        if (tierB && (store.pinnedKey(origin) == null || nowMs - lastKeyCheck > KEY_CHECK_MS)) {
            lastKeyCheck = nowMs
            syncCommandKey(origin, base, token)
        }
        for (c in AgentCommands.parseList(body)) {
            if (SignedCommand.isTierB(c)) {
                if (!timeOk) continue // not refused and not burned: it can be judged in a response with a sane clock
                if (!handleSigned(origin, base, token, c, serverTime)) break
                continue
            }
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

    // ---- signed (Tier B) commands (docs/signed-commands.md) -------------------------------

    /** Returns false when the command ended the process (reboot, install): stop looking at the rest. */
    private fun handleSigned(origin: String, base: String, token: String, c: Any?, serverTime: Long?): Boolean {
        val o = c as? JSONObject ?: return true
        val key = syncCommandKey(origin, base, token)
        val env = SignedCommand.Env(
            pinnedKey = key, keyChanged = store.keyChanged(origin),
            displayId = store.displayId.takeIf { it > 0 }, serverTimeMs = serverTime,
            lastSeq = store.lastSeq(origin), settings = settings, caps = caps(),
        )
        return when (val r = SignedCommand.verify(o, env, store.handledSet())) {
            is SignedCommand.Result.Ignore -> true
            is SignedCommand.Result.Reject -> {
                store.markHandled(r.id)
                FileLog.w(TAG, "signed command ${r.id} refused: ${r.reason}")
                ack(base, token, r.id, "failed", r.reason)
                true
            }
            is SignedCommand.Result.Accept -> {
                store.saveSeq(origin, r.seq) // before acting: it runs at most once
                store.markHandled(r.id)
                FileLog.i(TAG, "signed command ${r.id}: ${o.opt("action")} accepted")
                runSigned(base, token, r)
            }
        }
    }

    /**
     * Pins the server's command key the first time (trust on first use) and notices a later change.
     * Returns the pinned key. A failed fetch changes nothing.
     */
    private fun syncCommandKey(origin: String, base: String, token: String): ByteArray? {
        val pinned = store.pinnedKey(origin)
        val fetched = try {
            val req = Request.Builder().url("$base/command-key").header("Authorization", "Bearer $token").build()
            http().newCall(req).execute().use { r -> if (r.isSuccessful) KeyPin.parseKey(r.peekBody(MAX_BODY).string()) else null }
        } catch (e: Exception) {
            FileLog.w(TAG, "command key fetch failed: ${e.javaClass.simpleName}")
            null
        }
        return when (val d = KeyPin.decide(pinned, fetched)) {
            is KeyPin.Decision.Pin -> {
                store.pinKey(origin, d.key)
                store.setKeyChanged(origin, false)
                FileLog.i(TAG, "pinned the server's command key ${KeyPin.fingerprint(d.key)}")
                d.key
            }
            is KeyPin.Decision.Same -> { store.setKeyChanged(origin, false); pinned }
            is KeyPin.Decision.Changed -> {
                if (!store.keyChanged(origin)) FileLog.w(TAG, "the server's command key changed; signed commands are refused")
                store.setKeyChanged(origin, true)
                pinned
            }
            is KeyPin.Decision.Unusable -> pinned
        }
    }

    /** True to go on with the next command, false when the process is about to end (reboot, install). */
    private fun runSigned(base: String, token: String, r: SignedCommand.Result.Accept): Boolean =
        when (val a = r.action) {
            is SignedCommand.Action.Reboot -> { reboot(base, token, r.id); false }
            is SignedCommand.Action.Startup -> { setStartup(base, token, r.id, a); true }
            is SignedCommand.Action.Update -> !update(base, token, r.id, a)
        }

    private fun reboot(base: String, token: String, id: String) {
        val now = System.currentTimeMillis()
        if (!TierRules.rebootAllowed(store.lastRebootMs, now)) {
            ack(base, token, id, "failed", SignedCommand.NOT_ALLOWED)
            FileLog.w(TAG, "reboot refused: less than 10 minutes since the last one")
            return
        }
        if (!ack(base, token, id, "done", "rebooting")) return // the server never heard: do not act on a command it still shows as open
        store.lastRebootMs = now // after the ack, right before the reboot: a failed ack does not use up the cooldown
        try {
            Thread.sleep(1_500L) // let the ack and the log reach their destinations
            val dpm = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
            dpm.reboot(LockTask.admin(ctx))
        } catch (e: Exception) {
            FileLog.w(TAG, "reboot failed: ${e.javaClass.simpleName}")
        }
    }

    private fun setStartup(base: String, token: String, id: String, a: SignedCommand.Action.Startup) {
        try {
            // Decide first: a refused mode changes nothing (not even autostart).
            val d = StartupRules.decide(a.mode, HomeMode.lockKeepsHome(ctx))
            if (d is StartupRules.Decision.Refuse) {
                ack(base, token, id, "failed", d.result)
                FileLog.w(TAG, "set_startup refused: ${d.result}")
                return
            }
            a.autostart?.let { Installer.setAutostart(ctx, it) }
            (d as StartupRules.Decision.Apply).homeMode?.let { on ->
                if (!HomeMode.apply(ctx, on)) { ack(base, token, id, "failed", "could not change the Home app setting"); return }
            }
            val mode = StartupRules.reportedMode(app.config.homeMode, LockdownApplier.engaged(ctx))
            ack(base, token, id, "done", StartupRules.doneText(Installer.autostart(ctx), mode))
        } catch (e: Exception) {
            FileLog.w(TAG, "set_startup failed: ${e.javaClass.simpleName}")
            ack(base, token, id, "failed", "failed")
        }
    }

    /** Returns true when the install was started (the process may be replaced any moment). */
    private fun update(base: String, token: String, id: String, a: SignedCommand.Action.Update): Boolean {
        if (TierRules.alreadyInstalled(BuildConfig.VERSION_NAME, a.version)) {
            ack(base, token, id, "done", "already ${a.version}")
            return false
        }
        val file = Installer.updateFile(ctx)
        if (!TierRules.enoughSpace(try { ctx.filesDir.usableSpace } catch (_: Exception) { 0L }, a.size)) {
            ack(base, token, id, "failed", "not enough space")
            return false
        }
        val client = http().newBuilder().readTimeout(60, TimeUnit.SECONDS).build()
        when (val d = PackageDownload.fetch(client, "$base/commands/$id/package", token, file, a.size, a.sha256)) {
            is PackageDownload.Outcome.Failed -> {
                FileLog.w(TAG, "update download: ${d.why}")
                ack(base, token, id, "failed", d.why)
                Installer.cleanUp(ctx)
                return false
            }
            PackageDownload.Outcome.Ok -> Unit
        }
        store.updatePendingMs = System.currentTimeMillis()
        store.updateVersion = a.version
        if (!ack(base, token, id, "done", "installing ${a.version}")) { // ack BEFORE the install
            store.updatePendingMs = 0L
            Installer.cleanUp(ctx)
            return false
        }
        val why = Installer.install(ctx, file)
        if (why != null) {
            // The ack said "installing": the only place left to tell the admin is the next heartbeat.
            store.updateError = TierRules.storeUpdateError(a.version, why, System.currentTimeMillis())
            store.updatePendingMs = 0L
            Installer.cleanUp(ctx)
            return false
        }
        return true
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
        private const val KEY_CHECK_MS = 10 * 60_000L
        private const val FIRST_POLL_S = 10L
        private const val MAX_BODY = 64L * 1024
        private const val MAX_LOG_READ = 2 * 1024 * 1024
    }
}
