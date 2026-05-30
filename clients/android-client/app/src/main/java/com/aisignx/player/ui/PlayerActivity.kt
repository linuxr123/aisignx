package com.aisignx.player.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.graphics.Color
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.webkit.*
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.aisignx.player.ApiClient
import com.aisignx.player.Config
import com.aisignx.player.FileLog
import com.aisignx.player.SseClient
import com.aisignx.player.SignageApp
import com.aisignx.player.Updater
import com.aisignx.player.WebCache
import com.aisignx.player.databinding.ActivityPlayerBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import kotlin.system.exitProcess

class PlayerActivity : AppCompatActivity() {

    private lateinit var b: ActivityPlayerBinding

    @Volatile
    private var displayServerAutoUpdate: Boolean = false
    private var _updateCheckJob: Job? = null
    @Volatile
    private var _updateInProgress: Boolean = false
    @Volatile
    private var _playerPageLoaded: Boolean = false

    private fun effectiveUpdateMode(): String {
        if (displayServerAutoUpdate) return SignageApp.UPDATE_MODE_AUTO
        return Config.updateMode
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (!Config.isConfigured) {
            startActivity(Intent(this, SetupActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            })
            return
        }

        b = ActivityPlayerBinding.inflate(layoutInflater)
        setContentView(b.root)
        hideSystemUi()      // must come AFTER setContentView -- decor view
                            // doesn't exist before that on Android 14+ (Samsung)

        with(b.webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            // We do our own caching via WebCache (OkHttp disk cache). The
            // WebView's built-in cache is also enabled at default level so
            // anything that falls through (3rd-party resources from inside
            // plugins, Range video requests, etc.) still benefits.
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            useWideViewPort = true
            loadWithOverviewMode = true
        }

        // We deliberately leave long-press ENABLED on the WebView so the
        // in-page PIN unlock keypad (display_player.js) can detect a 1.5s
        // hold. Native text-selection / share menus that would otherwise
        // pop up on long-press are suppressed by overriding ActionMode
        // creation below (startActionMode hooks return null, blocking the
        // selection bar from ever appearing).
        b.webView.isHapticFeedbackEnabled = false
        b.webView.setOnLongClickListener {
            it.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
            b.webView.evaluateJavascript(
                "try{if(window.AISignXPromptUnlock)window.AISignXPromptUnlock();}catch(e){}",
                null
            )
            true
        }

        b.webView.isFocusable = true
        b.webView.isFocusableInTouchMode = true
        b.webView.setBackgroundColor(Color.BLACK)
        b.webView.requestFocus()

        b.webView.webViewClient = object : WebViewClient() {
            // ----- Disk-cache interceptor -----
            // Mirrors static/sw.js routing rules. WebCache returns null for
            // requests we don't want to handle (POSTs, Range, third-party,
            // SSE, /api/, /ping) — those fall through to the WebView's
            // default networking unchanged.
            override fun shouldInterceptRequest(
                view: WebView,
                req: WebResourceRequest
            ): WebResourceResponse? = WebCache.intercept(this@PlayerActivity, req)

            override fun onReceivedError(
                view: WebView,
                req: WebResourceRequest,
                err: WebResourceError
            ) {
                // Only react to main-frame failures. Sub-resource failures
                // (a missing image inside a plugin) shouldn't tear down the
                // whole player.
                if (!req.isForMainFrame) return
                FileLog.w(TAG, "main-frame load failed: ${err.errorCode} ${err.description}")
                showOfflinePage()
                scheduleOfflineRetry()
            }

            override fun onReceivedHttpError(
                view: WebView,
                req: WebResourceRequest,
                errorResponse: WebResourceResponse
            ) {
                if (!req.isForMainFrame) return
                FileLog.w(TAG, "main-frame HTTP error: ${errorResponse.statusCode} ${errorResponse.reasonPhrase}")
                showError(
                    "The display setup file was loaded, but the server rejected the player page.\n\n" +
                    "HTTP ${errorResponse.statusCode}: ${errorResponse.reasonPhrase}\n\n" +
                    "Check that this display still exists and that the setup file came from the correct server."
                )
            }

            override fun onPageFinished(view: WebView, url: String) {
                // If the offline page just finished, leave the retry timer
                // running. If the real player URL just loaded, cancel it.
                if (url.startsWith("file:///android_asset/offline.html")) return
                _playerPageLoaded = true
                cancelOfflineRetry()
                _showingOffline = false
                val version = try {
                    packageManager.getPackageInfo(packageName, 0).versionName ?: "1.0.0"
                } catch (_: Throwable) { "1.0.0" }
                view.evaluateJavascript(
                    "window.AISIGNX_APP_VERSION=${org.json.JSONObject.quote(version)};",
                    null
                )
                view.evaluateJavascript(
                    "window.AISIGNX_NATIVE_CLIENT='android';",
                    null
                )
                view.evaluateJavascript(
                    "(function(){try{return !!(window.AISIGNX_AUTO_UPDATE_CLIENT);}catch(e){return false;}})();"
                ) { res ->
                    val v = (res?.trim()?.equals("true", ignoreCase = true) == true)
                    displayServerAutoUpdate = v
                }
            }

            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest) = false
        }

        b.webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(msg: ConsoleMessage) = false
        }

        b.webView.addJavascriptInterface(NativePlayerBridge(), "AISignXNative")

        b.btnRetry.setOnClickListener { loadPlayer() }
        b.btnReset.setOnClickListener { resetSetup() }

        loadPlayer()
        scheduleUpdateChecks()
        startSseListener()
    }

    private var _lockTaskJob: Job? = null

    override fun onDestroy() {
        cancelLockTaskWatchdog()
        _updateCheckJob?.cancel()
        super.onDestroy()
        SseClient.stop()
    }

    override fun onResume() {
        super.onResume()
        // If we come back to the foreground after a PIN unlock-minimize —
        // whether the user reopened us or the idle timer relaunched us — treat
        // it as "exit unlock mode": cancel the idle timer, re-engage the kiosk
        // lock task, and tell the player page to re-lock its PIN.
        val wasUnlocked = _unlockMinimized
        if (_unlockMinimized) {
            _unlockMinimized = false
            _idleRestoreJob?.cancel()
            _idleRestoreJob = null
        }
        engageLockTask()
        startLockTaskWatchdog()
        if (wasUnlocked) {
            b.webView.evaluateJavascript("window.AISignXRelock && window.AISignXRelock();", null)
        }
    }

    override fun onPause() {
        cancelLockTaskWatchdog()
        super.onPause()
    }

    // ── PIN unlock → background → idle/manual restore ────────────────────────
    @Volatile
    private var _unlockMinimized = false
    private var _idleRestoreJob: Job? = null
    private val IDLE_RESTORE_MS = 5L * 60 * 1000

    private fun beginUnlockMinimize() {
        FileLog.i(TAG, "PIN unlock — backgrounding for desktop access")
        _unlockMinimized = true
        cancelLockTaskWatchdog()
        try { stopLockTask() } catch (_: Throwable) {}
        _idleRestoreJob?.cancel()
        _idleRestoreJob = lifecycleScope.launch {
            delay(IDLE_RESTORE_MS)
            if (_unlockMinimized) {
                withContext(Dispatchers.Main) { restoreFromUnlock("idle") }
            }
        }
        try { moveTaskToBack(true) } catch (_: Throwable) {}
    }

    private fun restoreFromUnlock(reason: String) {
        if (!_unlockMinimized) return
        FileLog.i(TAG, "restoring kiosk after unlock ($reason)")
        // Bring our activity back to the front; onResume() handles the actual
        // re-lock + lock-task re-engagement and clears the minimized flag.
        val intent = packageManager.getLaunchIntentForPackage(packageName)
        if (intent != null) {
            intent.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_NEW_TASK)
            try { startActivity(intent) } catch (_: Throwable) {}
        }
    }

    private fun engageLockTask() {
        // Re-engage screen pinning every time the activity becomes
        // foreground. If the user is granted screen pinning permission
        // via Settings -> Security -> Screen Pinning, this will lock the
        // device to our app: home/recents/back are blocked, and exiting
        // requires the device PIN. If the user hasn't enabled the
        // permission, startLockTask() is a no-op (no crash, no prompt).
        try {
            if (!isInLockTaskMode()) startLockTask()
        } catch (_: Throwable) { /* device doesn't support, ignore */ }
    }

    private fun startLockTaskWatchdog() {
        cancelLockTaskWatchdog()
        _lockTaskJob = lifecycleScope.launch {
            while (isActive) {
                delay(2_000L)
                if (!isInLockTaskMode()) {
                    FileLog.i(TAG, "lock task exited — re-engaging")
                    withContext(Dispatchers.Main) { engageLockTask() }
                }
            }
        }
    }

    private fun cancelLockTaskWatchdog() {
        _lockTaskJob?.cancel()
        _lockTaskJob = null
    }

    private fun isInLockTaskMode(): Boolean {
        return try {
            val am = getSystemService(android.content.Context.ACTIVITY_SERVICE)
                as android.app.ActivityManager
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                am.lockTaskModeState != android.app.ActivityManager.LOCK_TASK_MODE_NONE
            } else {
                @Suppress("DEPRECATION")
                am.isInLockTaskMode
            }
        } catch (_: Throwable) { false }
    }

    // Suppress the floating text-selection action bar (cut/copy/share/etc.)
    // that the WebView would otherwise pop up on long-press. We need
    // long-press itself to keep working so the in-page PIN keypad can
    // detect a 1.5-second hold; we just don't want the OS context bar
    // to appear on top of it.
    override fun onActionModeStarted(mode: android.view.ActionMode?) {
        try { mode?.finish() } catch (_: Throwable) {}
    }
    override fun startActionMode(callback: android.view.ActionMode.Callback?): android.view.ActionMode? = null
    override fun startActionMode(callback: android.view.ActionMode.Callback?, type: Int): android.view.ActionMode? = null

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    /**
     * Connects to /display/<token>/events and reacts to admin-pushed commands.
     * Mirrors the Electron client behaviour: 'reload' refreshes the WebView,
     * 'reboot' restarts the activity, 'update' fetches and installs the
     * latest APK from /api/version.
     */
    private fun startSseListener() {
        SseClient.start(lifecycleScope, Config.serverUrl, Config.token, Config.deviceId)
        lifecycleScope.launch {
            SseClient.events.collect { ev ->
                when (ev.type) {
                    "command" -> handleCommand(ev.data)
                    "reload"  -> withContext(Dispatchers.Main) { b.webView.reload() }
                    // Other events (settings, emergency) are handled by the
                    // player page JS itself, no native action needed.
                }
            }
        }
        // Reload the WebView when SSE recovers from a disconnected state.
        // This handles the cold-start-while-server-down case: PlayerActivity
        // starts → loadPlayer() shows offline.html → SSE eventually connects
        // when server returns → we reload the real URL automatically.
        lifecycleScope.launch {
            var prev = false
            SseClient.connected.collect { now ->
                if (!now && prev) {
                    withContext(Dispatchers.Main) {
                        b.webView.evaluateJavascript(
                            "window.signageReportConnectivity && window.signageReportConnectivity('server-offline');",
                            null
                        )
                    }
                }
                if (now && !prev && _showingOffline) {
                    FileLog.i(TAG, "SSE reconnected — reloading player")
                    withContext(Dispatchers.Main) { loadPlayer() }
                }
                prev = now
            }
        }
    }

    private suspend fun handleCommand(data: String) {
        val action = try { JSONObject(data).optString("action").lowercase() }
                     catch (_: Exception) { "" }
        FileLog.i(TAG, "command received: $action")
        when (action) {
            "reload" -> withContext(Dispatchers.Main) { b.webView.reload() }
            "reboot" -> withContext(Dispatchers.Main) { restartActivity() }
            "update" -> withContext(Dispatchers.IO)   {
                val ok = Updater.runUpdate(this@PlayerActivity)
                if (!ok) FileLog.w(TAG, "update did not start")
            }
            "release_device_owner" -> withContext(Dispatchers.Main) { releaseDeviceOwner() }
            else     -> FileLog.w(TAG, "unknown command: $action")
        }
    }

    /**
     * Relinquish Device Owner so the kiosk can be un-provisioned without a
     * factory reset. After this the app is an ordinary app: lock-task / screen
     * pinning is released and the app can be uninstalled normally. This is the
     * supported "undo" for `dpm set-device-owner` — Android does not expose a
     * Settings toggle to remove a Device Owner.
     */
    private fun releaseDeviceOwner() {
        try {
            val dpm = getSystemService(android.content.Context.DEVICE_POLICY_SERVICE)
                as android.app.admin.DevicePolicyManager
            if (!dpm.isDeviceOwnerApp(packageName)) {
                FileLog.i(TAG, "release_device_owner: not a device owner; nothing to do")
                return
            }
            // Stop pinning first so we're not holding a lock task while we drop
            // the privilege that lets us re-engage it.
            try { stopLockTask() } catch (_: Throwable) {}
            cancelLockTaskWatchdog()
            dpm.clearDeviceOwnerApp(packageName)
            FileLog.i(TAG, "release_device_owner: cleared device owner")
        } catch (t: Throwable) {
            FileLog.e(TAG, "release_device_owner failed", t)
        }
    }

    private fun restartActivity() {
        val pm = packageManager
        val intent = pm.getLaunchIntentForPackage(packageName) ?: return
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        startActivity(intent)
        finishAffinity()
        // Give Android a beat to actually finish us before we exit
        b.webView.postDelayed({ exitProcess(0) }, 200)
    }

    private fun scheduleUpdateChecks(runImmediately: Boolean = false) {
        if (effectiveUpdateMode() == SignageApp.UPDATE_MODE_MANUAL) {
            _updateCheckJob?.cancel()
            _updateCheckJob = null
            return
        }
        if (_updateCheckJob?.isActive == true) {
            if (runImmediately) lifecycleScope.launch(Dispatchers.IO) { checkForUpdate() }
            return
        }
        _updateCheckJob = lifecycleScope.launch(Dispatchers.IO) {
            if (runImmediately) {
                checkForUpdate()
                delay(15L * 60 * 1000)
            } else {
                delay(10_000L)
            }
            while (isActive) {
                checkForUpdate()
                delay(15L * 60 * 1000)
            }
        }
    }

    private suspend fun checkForUpdate() {
        if (_updateInProgress) return
        val serverUrl = Config.serverUrl
        if (serverUrl.isBlank()) return
        val currentVersion = packageManager
            .getPackageInfo(packageName, 0).versionName ?: "1.0.0"
        val info = ApiClient.checkForUpdate(serverUrl, currentVersion) ?: return
        when (effectiveUpdateMode()) {
            SignageApp.UPDATE_MODE_AUTO -> {
                FileLog.i(TAG, "auto update: installing ${info.version}")
                _updateInProgress = true
                try {
                    val ok = Updater.runUpdate(this@PlayerActivity)
                    if (!ok) FileLog.w(TAG, "auto update did not start")
                } finally {
                    _updateInProgress = false
                }
            }
            SignageApp.UPDATE_MODE_PROMPT -> withContext(Dispatchers.Main) {
                AlertDialog.Builder(this@PlayerActivity)
                    .setTitle("Update Available")
                    .setMessage("New version ${info.version} is available (you have $currentVersion).\n\nDownload it now?")
                    .setPositiveButton("Update Now") { _, _ ->
                        lifecycleScope.launch(Dispatchers.IO) { Updater.runUpdate(this@PlayerActivity) }
                    }
                    .setNeutralButton("Open in Browser") { _, _ ->
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(info.downloadUrl)))
                    }
                    .setNegativeButton("Later", null)
                    .show()
            }
            else -> Unit
        }
    }

    private fun loadPlayer() {
        b.errorLayout.visibility = View.GONE
        b.webView.visibility = View.VISIBLE
        cancelOfflineRetry()
        val url = "${Config.serverUrl}/display/${Config.token}?client_id=${Uri.encode(Config.deviceId)}"
        _lastPlayerUrl = url
        _playerPageLoaded = false
        b.webView.loadUrl(url)
        b.webView.postDelayed({
            if (!_playerPageLoaded && !_showingOffline) {
                FileLog.w(TAG, "player page load timeout: $url")
                showOfflinePage()
                scheduleOfflineRetry()
            }
        }, 12_000L)
    }

    // ── Offline cold-start handling ──────────────────────────────────────────
    // When the WebView fails to load /display/<token> (server unreachable
    // AND OkHttp cache is empty — typically cold start with the server
    // down), we show a packaged offline.html page and retry the real URL
    // every 10s. Once SSE reconnects (see startSseListener), loadPlayer()
    // is called again and the offline page is replaced by the real player.
    private var _lastPlayerUrl: String? = null
    private var _showingOffline: Boolean = false
    private var _retryJob: Job? = null
    private val RETRY_INTERVAL_MS = 10_000L

    private fun showOfflinePage() {
        if (_showingOffline) return
        _showingOffline = true
        val hash = android.net.Uri.encode(_lastPlayerUrl ?: "")
        b.webView.loadUrl("file:///android_asset/offline.html#$hash")
    }

    private fun scheduleOfflineRetry() {
        cancelOfflineRetry()
        _retryJob = lifecycleScope.launch {
            while (isActive && _showingOffline) {
                delay(RETRY_INTERVAL_MS)
                if (!_showingOffline) break
                FileLog.i(TAG, "offline retry: trying $_lastPlayerUrl")
                withContext(Dispatchers.Main) {
                    _lastPlayerUrl?.let { b.webView.loadUrl(it) }
                }
            }
        }
    }

    private fun cancelOfflineRetry() {
        _retryJob?.cancel()
        _retryJob = null
    }

    private fun showError(msg: String) {
        b.webView.visibility = View.GONE
        b.errorLayout.visibility = View.VISIBLE
        b.tvError.text = msg
    }

    private fun resetSetup() {
        Config.clear()
        startActivity(Intent(this, SetupActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        })
    }

    // Block back for kiosk mode. Remote keys are delivered to the WebView so
    // the in-page PIN keypad can use D-pad focus; MENU still opens the keypad.
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) return true
        event?.let { ev ->
            if (b.webView.dispatchKeyEvent(ev)) return true
        }
        if (keyCode == KeyEvent.KEYCODE_MENU && event?.repeatCount == 0) {
            promptPinKeypad()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    private fun promptPinKeypad() {
        b.webView.evaluateJavascript(
            "window.promptSignagePin && window.promptSignagePin();",
            null
        )
    }

    override fun onBackPressed() { /* kiosk â€” do nothing */ }

    private fun hideSystemUi() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let {
                it.hide(WindowInsets.Type.systemBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            )
        }
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
            WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD or
            WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
        )
    }

    companion object {
        private const val TAG = "AISignX/Player"
    }

    private inner class NativePlayerBridge {
        @android.webkit.JavascriptInterface
        fun prefetchPlaylist(mediaJson: String, pluginJson: String, pagePath: String) {
            val appCtx = this@PlayerActivity.applicationContext
            try {
                val media = org.json.JSONArray(mediaJson)
                val plugins = org.json.JSONArray(pluginJson)
                val base = Config.serverUrl.trimEnd('/')
                fun absoluteUrl(value: String): String {
                    val trimmed = value.trim()
                    if (trimmed.startsWith("http://", ignoreCase = true) ||
                        trimmed.startsWith("https://", ignoreCase = true)) {
                        return trimmed
                    }
                    return "$base/${trimmed.trimStart('/')}"
                }
                val mediaUrls = buildList {
                    for (i in 0 until media.length()) {
                        media.optString(i).takeIf { it.isNotBlank() }?.let { add(absoluteUrl(it)) }
                    }
                }
                val pluginUrls = buildList {
                    for (i in 0 until plugins.length()) {
                        plugins.optString(i).takeIf { it.isNotBlank() }?.let { add(absoluteUrl(it)) }
                    }
                }
                val pageUrls = if (pagePath.isNotBlank()) listOf(absoluteUrl(pagePath)) else emptyList()
                WebCache.prefetchPlaylist(appCtx, mediaUrls, pluginUrls, pageUrls)
            } catch (t: Throwable) {
                FileLog.w(TAG, "prefetchPlaylist failed: ${t.message}")
            }
        }

        @android.webkit.JavascriptInterface
        fun setDisplayAutoUpdateClient(enabled: Boolean) {
            runOnUiThread {
                displayServerAutoUpdate = enabled
                scheduleUpdateChecks(runImmediately = enabled)
            }
        }

        // Called by display_player.js after a correct PIN. Drops out of the
        // kiosk lock task and sends the app to the background so a technician
        // can use the device. The app stays backgrounded until the idle timer
        // fires or the user reopens it, at which point we re-lock (onResume).
        @android.webkit.JavascriptInterface
        fun unlockMinimize() {
            runOnUiThread { beginUnlockMinimize() }
        }
    }
}
