package com.aisignx.player.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebSettings
import android.webkit.WebView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.aisignx.player.AiSignXApp
import com.aisignx.player.BuildConfig
import com.aisignx.player.FileLog
import com.aisignx.player.R
import com.aisignx.player.databinding.ActivityPlayerBinding
import com.aisignx.player.shell.ShellInfo
import com.aisignx.player.shell.ShellPort

/**
 * Kiosk shell around the v3 browser player at <server>/player/. Pairing, playback,
 * schedules, emergency rules and SSE all live in the page; this activity only keeps
 * the WebView alive, full screen and awake.
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = AiSignXApp.from(this)
        if (app.services() == null) {
            openSetup()
            return
        }
        b = ActivityPlayerBinding.inflate(layoutInflater)
        setContentView(b.root)
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
        view.addJavascriptInterface(
            ShellInfo.forThisDevice(app.config.clientId, services.origin),
            ShellInfo.JS_NAME,
        )
        val port = ShellPort(
            onState = { s -> FileLog.i(TAG, "player state $s") },
            onManifest = { json -> services.mediaCache.onManifest(json) },
        )
        port.attach(view, services.origin)
        shellPort = port

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

    /** 7 taps in the top-left 80dp within 3 s. Counted without consuming the touches. */
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
        return super.dispatchTouchEvent(ev)
    }

    @Deprecated("Kiosk: the back key never leaves the player")
    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        // Swallowed on purpose.
    }

    private fun confirmExit() {
        if (exitDialog?.isShowing == true) return
        exitDialog = AlertDialog.Builder(this)
            .setTitle(R.string.exit_title)
            .setMessage(R.string.exit_message)
            .setPositiveButton(R.string.exit_yes) { _, _ -> openSetup() }
            .setNegativeButton(R.string.exit_no, null)
            .setOnDismissListener { Kiosk.hideSystemUi(this) }
            .show()
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
        private const val CRASH_BUDGET = 5
        private const val CRASH_WINDOW_MS = 10 * 60 * 1000L

        /** Survives recreate() so the budget spans activity instances. */
        private val crashTimes = ArrayList<Long>()
    }
}
