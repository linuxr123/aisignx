package com.aisignx.player.ui

import android.annotation.SuppressLint
import android.app.UiModeManager
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.os.Process
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputFilter
import android.text.InputType
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebSettings
import android.webkit.WebView
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.aisignx.player.AiSignXApp
import com.aisignx.player.BuildConfig
import com.aisignx.player.FileLog
import com.aisignx.player.R
import com.aisignx.player.databinding.ActivityPlayerBinding
import com.aisignx.player.shell.Capture
import com.aisignx.player.shell.DisplaySettings
import com.aisignx.player.shell.ShellInfo
import com.aisignx.player.shell.ShellPort
import com.aisignx.player.shell.Unlock
import com.aisignx.player.shell.UnlockHash
import java.util.concurrent.Executors

/**
 * Kiosk shell around the v3 browser player at <server>/player/. Pairing, playback,
 * schedules, emergency rules and SSE all live in the page; this activity only keeps
 * the WebView alive, full screen and awake, and applies the native part of the
 * display settings: orientation (not on TVs), the input lock and the unlock PIN.
 */
class PlayerActivity : AppCompatActivity() {

    private lateinit var b: ActivityPlayerBinding
    private var webView: WebView? = null
    private var webClient: PlayerWebClient? = null
    private var shellPort: ShellPort? = null
    private val main = Handler(Looper.getMainLooper())

    private var exitKeyDownAt = 0L
    private var exitKeyFired = false
    private val tapTimes = ArrayDeque<Long>()
    private var exitDialog: AlertDialog? = null
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "aisignx-player").apply { isDaemon = true } }
    private var isTv = false
    private var inputLocked = false
    private var verifying = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = AiSignXApp.from(this)
        if (app.services() == null) {
            openSetup()
            return
        }
        b = ActivityPlayerBinding.inflate(layoutInflater)
        setContentView(b.root)
        isTv = (getSystemService(UI_MODE_SERVICE) as? UiModeManager)
            ?.currentModeType == Configuration.UI_MODE_TYPE_TELEVISION
        applyStoredSettings()
        Kiosk.keepAwake(this)
        Kiosk.hideSystemUi(this)
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        createWebView()
    }

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    private fun createWebView() {
        val app = AiSignXApp.from(this)
        val services = app.services() ?: return openSetup()
        val view = WebView(this)
        view.setBackgroundColor(Color.BLACK)
        view.isFocusable = false
        view.isFocusableInTouchMode = false
        with(view.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            cacheMode = WebSettings.LOAD_DEFAULT
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(view, false)
        }
        // The port comes first: settings and capture requests arrive through it,
        // so the caps reported by ShellInfo depend on whether it attached.
        val port = ShellPort(
            onState = { s -> FileLog.i(TAG, "player state $s") },
            onManifest = { json -> onManifest(json) },
            onCapture = { _, reply -> Capture.capture(this, worker, reply) },
        )
        val attached = port.attach(view, services.origin)
        shellPort = port
        view.addJavascriptInterface(
            ShellInfo.forThisDevice(
                app.config.clientId,
                services.origin,
                ShellInfo.caps(attached, isTv),
            ) { stats() },
            ShellInfo.JS_NAME,
        )

        val client = PlayerWebClient(services, object : PlayerWebClient.Callbacks {
            override fun offlineReason(certChanged: Boolean): String = getString(
                if (certChanged) R.string.reason_cert_changed else R.string.reason_offline,
            )

            override fun onRenderProcessGone(view: WebView): Boolean {
                main.post { recoverFromRendererCrash(view) }
                return true
            }
        })
        view.webViewClient = client
        b.playerRoot.addView(view, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        webView = view
        webClient = client
        client.start(view)
    }

    /** Worker thread (ShellPort). Settings first, then the media prefetch. */
    private fun onManifest(json: String) {
        val app = AiSignXApp.from(this)
        try {
            DisplaySettings.parse(json)?.let { s ->
                app.config.saveDisplaySettings(s)
                main.post { if (!isFinishing && !isDestroyed) applySettings(s) }
            }
        } catch (e: Exception) {
            FileLog.w(TAG, "display settings: ${e.message}")
        }
        app.services()?.mediaCache?.onManifest(json)
    }

    /** Settings saved from the last manifest, so an offline boot still applies them. */
    private fun applyStoredSettings() {
        val config = AiSignXApp.from(this).config
        val deg = config.displayOrientation
        if (deg != -1 && !isTv) requestedOrientation = DisplaySettings.orientationFlag(deg)
        inputLocked = !config.allowInput
    }

    private fun applySettings(s: DisplaySettings) {
        if (!isTv) {
            val flag = DisplaySettings.orientationFlag(s.orientation)
            if (requestedOrientation != flag) requestedOrientation = flag
        }
        if (inputLocked == s.allowInput) FileLog.i(TAG, "input ${if (s.allowInput) "allowed" else "locked"}")
        inputLocked = !s.allowInput
    }

    /** JavaBridge thread. No secrets: iframes can call this too. */
    private fun stats(): String {
        val uptime = (SystemClock.uptimeMillis() - Process.getStartUptimeMillis()) / 1000
        val cache = AiSignXApp.from(this).services()?.mediaCache
        val free = try { filesDir.usableSpace } catch (_: Exception) { -1L }
        return ShellInfo.statsJson(uptime, cache?.fileCount() ?: 0, cache?.totalBytes() ?: 0L, free)
    }

    private fun destroyWebView() {
        webClient?.dispose()
        webClient = null
        shellPort?.close()
        shellPort = null
        webView?.let { v ->
            b.playerRoot.removeView(v)
            try { v.destroy() } catch (_: Exception) { }
        }
        webView = null
    }

    /** Recreate the WebView after a renderer crash; too many crashes recreate the activity. */
    private fun recoverFromRendererCrash(gone: WebView) {
        if (gone !== webView || isFinishing) return
        val now = SystemClock.elapsedRealtime()
        crashTimes.removeAll { now - it > CRASH_WINDOW_MS }
        crashTimes.add(now)
        destroyWebView()
        if (crashTimes.size > CRASH_BUDGET) {
            FileLog.w(TAG, "more than $CRASH_BUDGET renderer crashes in 10 minutes, recreating activity")
            crashTimes.clear()
            recreate()
            return
        }
        main.postDelayed({ if (!isFinishing && webView == null) createWebView() }, 2_000L)
    }

    override fun onResume() {
        super.onResume()
        webView?.onResume()
        webView?.resumeTimers()
        if (::b.isInitialized) Kiosk.hideSystemUi(this)
    }

    override fun onPause() {
        webView?.onPause()
        webView?.pauseTimers()
        CookieManager.getInstance().flush()
        super.onPause()
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        exitDialog?.dismiss()
        worker.shutdownNow()
        if (::b.isInitialized) destroyWebView()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && ::b.isInitialized) Kiosk.hideSystemUi(this)
    }

    // ---- keys and the hidden exit ---------------------------------------------------

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (TvKeys.isExitKey(code)) {
            when (event.action) {
                KeyEvent.ACTION_DOWN -> {
                    if (event.repeatCount == 0) {
                        exitKeyDownAt = event.eventTime
                        exitKeyFired = false
                    } else if (!exitKeyFired && event.eventTime - exitKeyDownAt >= LONG_PRESS_MS) {
                        exitKeyFired = true
                        confirmExit()
                    }
                }
                KeyEvent.ACTION_UP -> {
                    if (!exitKeyFired && event.eventTime - exitKeyDownAt >= LONG_PRESS_MS) confirmExit()
                    exitKeyFired = false
                }
            }
            return true
        }
        if (TvKeys.consumes(code)) return true
        return super.dispatchKeyEvent(event)
    }

    /**
     * 7 taps in the top-left 80dp within 3 s, counted even while input is locked.
     * Otherwise the touches go to the page unless the admin turned input off.
     */
    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.actionMasked == MotionEvent.ACTION_DOWN) {
            val corner = 80 * resources.displayMetrics.density
            if (ev.x <= corner && ev.y <= corner) {
                val now = SystemClock.uptimeMillis()
                tapTimes.addLast(now)
                while (tapTimes.isNotEmpty() && now - tapTimes.first() > TAP_WINDOW_MS) tapTimes.removeFirst()
                if (tapTimes.size >= TAP_COUNT) {
                    tapTimes.clear()
                    confirmExit()
                }
            } else {
                tapTimes.clear()
            }
        }
        if (inputLocked) return true
        return super.dispatchTouchEvent(ev)
    }

    /** Mouse wheel and hover; keys stay with dispatchKeyEvent and the page. */
    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {
        if (inputLocked) return true
        return super.dispatchGenericMotionEvent(ev)
    }

    @Deprecated("Kiosk: the back key never leaves the player")
    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        // Swallowed on purpose.
    }

    /** The hidden exit. With an unlock PIN set it asks for the PIN instead of yes/no. */
    private fun confirmExit() {
        if (exitDialog?.isShowing == true || verifying) return
        val config = AiSignXApp.from(this).config
        val hash = config.unlockHash
        if (hash != null) {
            val wait = remainingWait(config.unlockLockedUntil)
            exitDialog = if (wait > 0) waitDialog(wait) else pinDialog(hash)
            return
        }
        exitDialog = AlertDialog.Builder(this)
            .setTitle(R.string.exit_title)
            .setMessage(R.string.exit_message)
            .setPositiveButton(R.string.exit_yes) { _, _ -> openSetup() }
            .setNegativeButton(R.string.exit_no, null)
            .setOnDismissListener { Kiosk.hideSystemUi(this) }
            .show()
    }

    private fun waitDialog(waitMs: Long): AlertDialog = AlertDialog.Builder(this)
        .setTitle(R.string.unlock_title)
        .setMessage(getString(R.string.unlock_wait, waitText(waitMs)))
        .setPositiveButton(android.R.string.ok, null)
        .setOnDismissListener { Kiosk.hideSystemUi(this) }
        .show()

    private fun pinDialog(hash: UnlockHash): AlertDialog {
        val field = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
            filters = arrayOf(InputFilter.LengthFilter(MAX_PIN_LENGTH))
            setHint(R.string.unlock_hint)
            isSingleLine = true
        }
        val pad = (20 * resources.displayMetrics.density).toInt()
        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.unlock_title)
            .setPositiveButton(R.string.unlock_ok) { _, _ -> checkPin(field.text.toString(), hash) }
            .setNegativeButton(R.string.exit_no, null)
            .setOnDismissListener { Kiosk.hideSystemUi(this) }
            .create()
        dialog.setView(field, pad, pad / 2, pad, 0)
        dialog.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE)
        dialog.show()
        field.requestFocus()
        return dialog
    }

    /** PBKDF2 takes up to a second or so on weak boxes, so it runs on the worker. */
    private fun checkPin(pin: String, hash: UnlockHash) {
        verifying = true
        try {
            worker.execute {
                val ok = try { Unlock.verify(pin, hash) } catch (_: Exception) { false }
                main.post { onPinResult(ok) }
            }
        } catch (_: Exception) { // shutting down
            verifying = false
        }
    }

    private fun onPinResult(ok: Boolean) {
        verifying = false
        if (isFinishing || isDestroyed) return
        val config = AiSignXApp.from(this).config
        if (ok) {
            config.saveUnlockState(0, 0L)
            FileLog.i(TAG, "unlock PIN accepted")
            openSetup()
            return
        }
        val failures = config.unlockFailures + 1
        val wait = Unlock.waitMs(failures)
        config.saveUnlockState(failures, if (wait > 0) System.currentTimeMillis() + wait else 0L)
        FileLog.w(TAG, "wrong unlock PIN ($failures in a row)")
        val text = if (wait > 0) getString(R.string.unlock_wait, waitText(wait)) else getString(R.string.unlock_wrong)
        Toast.makeText(this, text, Toast.LENGTH_LONG).show()
    }

    private fun openSetup() {
        startActivity(
            Intent(this, SetupActivity::class.java)
                .putExtra(SetupActivity.EXTRA_RECONFIGURE, true)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP),
        )
        finish()
    }

    companion object {
        private const val TAG = "Player"
        private const val LONG_PRESS_MS = 1_500L
        private const val TAP_WINDOW_MS = 3_000L
        private const val TAP_COUNT = 7
        private const val MAX_PIN_LENGTH = 12
        private const val CRASH_BUDGET = 5
        private const val CRASH_WINDOW_MS = 10 * 60 * 1000L

        /** Survives recreate() so the budget spans activity instances. */
        private val crashTimes = ArrayList<Long>()

        /** Lockout left, capped so a clock set backwards cannot lock the screen for long. */
        fun remainingWait(lockedUntil: Long, now: Long = System.currentTimeMillis()): Long =
            (lockedUntil - now).coerceIn(0L, Unlock.MAX_WAIT_MS)

        /** "45 s" or "3 min", rounded up. */
        fun waitText(ms: Long): String {
            val s = (ms + 999) / 1000
            return if (s < 60) "$s s" else "${(s + 59) / 60} min"
        }
    }
}
