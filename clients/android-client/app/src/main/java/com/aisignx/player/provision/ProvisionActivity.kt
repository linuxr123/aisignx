package com.aisignx.player.provision

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.WindowManager
import android.widget.TextView
import com.aisignx.player.ui.PlayerActivity
import com.aisignx.player.ui.SetupActivity
import java.util.concurrent.Executors

/**
 * "Setting up this screen": claims the pairing that the QR code pre-approved, retrying while the
 * network is not up yet, then starts the player. If the claim can never work (the code expired,
 * the certificate is not trusted) the player simply starts and shows its normal pairing code.
 * Escapes: after [ProvisionRules.GIVE_UP_AFTER_MS] of trying, or [ProvisionRules.TAPS] quick taps
 * anywhere on the screen (then a confirmation), the app forgets the QR set-up and opens the normal
 * setup screen, where the address can be fixed and Device Owner removed.
 */
class ProvisionActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "aisignx-provision").apply { isDaemon = true } }
    private lateinit var text: TextView
    private val taps = ProvisionRules.TapCounter()
    @Volatile private var stopped = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        text = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 24f
            gravity = Gravity.CENTER
            setBackgroundColor(Color.BLACK)
            setPadding(48, 48, 48, 48)
            this.text = "Setting up this screen…"
            setOnClickListener { if (taps.tap(SystemClock.elapsedRealtime())) confirmExit() }
        }
        setContentView(text)
        if (!Provisioning.pending(this)) { startPlayer(); return }
        worker.execute { loop() }
    }

    private fun loop() {
        val started = SystemClock.elapsedRealtime()
        var attempt = 0
        while (!stopped) {
            val r = Provisioning.claim(this)
            when (r.claim) {
                ProvisionRules.Claim.PAIRED -> {
                    // One main-thread block: store the credential, check it, and only then forget the secret.
                    main.post {
                        if (Provisioning.storeCookies(this, r.cookies)) { Provisioning.complete(this); startPlayer() }
                        else text.text = "Setting up this screen…\nSaving the pairing"
                    }
                    if (!waitForCookies()) return
                }
                ProvisionRules.Claim.GIVE_UP -> {
                    main.post { Provisioning.complete(this); startPlayer() }
                    return
                }
                ProvisionRules.Claim.RETRY -> Unit
            }
            if (ProvisionRules.timedOut(started, SystemClock.elapsedRealtime())) {
                main.post { Provisioning.abandon(this); openSetup() }
                return
            }
            attempt++
            main.post { text.text = "Setting up this screen…\nWaiting for the network and server (attempt $attempt)" }
            try { Thread.sleep(ProvisionRules.delayMs(attempt)) } catch (_: InterruptedException) { return }
        }
    }

    /** After a PAIRED answer: false when the activity is done (the main thread finished it). */
    private fun waitForCookies(): Boolean {
        try { Thread.sleep(1_500L) } catch (_: InterruptedException) { return false }
        return !stopped && !isFinishing && Provisioning.pending(this)
    }

    private fun confirmExit() {
        if (isFinishing) return
        AlertDialog.Builder(this)
            .setTitle("Stop automatic set-up?")
            .setMessage("The app forgets the server address from the QR code and opens the setup screen.")
            .setNegativeButton("Keep waiting", null)
            .setPositiveButton("Stop") { _, _ -> stopped = true; Provisioning.abandon(this); openSetup() }
            .show()
    }

    private fun openSetup() {
        if (isFinishing) return
        startActivity(
            Intent(this, SetupActivity::class.java).putExtra(SetupActivity.EXTRA_RECONFIGURE, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        finish()
    }

    private fun startPlayer() {
        if (isFinishing) return
        startActivity(Intent(this, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finish()
    }

    override fun onDestroy() {
        stopped = true
        worker.shutdownNow()
        super.onDestroy()
    }
}
