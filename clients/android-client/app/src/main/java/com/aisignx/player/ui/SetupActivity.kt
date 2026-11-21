package com.aisignx.player.ui

import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import androidx.appcompat.app.AlertDialog
import android.view.inputmethod.EditorInfo
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.aisignx.player.AiSignXApp
import com.aisignx.player.BuildConfig
import com.aisignx.player.Config
import com.aisignx.player.FileLog
import com.aisignx.player.R
import com.aisignx.player.ServerUrl
import com.aisignx.player.databinding.ActivitySetupBinding
import com.aisignx.player.net.Http
import com.aisignx.player.net.Tls
import com.aisignx.player.provision.ProvisionActivity
import com.aisignx.player.provision.Provisioning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Server address, connection test and certificate trust (TOFU pin). D-pad friendly.
 * No password, token or code is ever typed on the device: pairing happens in the
 * browser player with a short code shown on screen.
 */
class SetupActivity : AppCompatActivity() {

    private lateinit var b: ActivitySetupBinding
    private lateinit var config: Config
    private val scope = MainScope()
    private var testJob: Job? = null

    /** The address and pin of the last successful test; Save needs it. */
    private var tested: Pair<String, String?>? = null
    private var captured: Tls.CertInfo? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = AiSignXApp.from(this).config
        val reconfigure = intent.getBooleanExtra(EXTRA_RECONFIGURE, false)
        if (Provisioning.pending(this)) { // QR set-up not finished: it claims the pairing first
            startActivity(Intent(this, ProvisionActivity::class.java))
            finish()
            return
        }
        if (config.isConfigured && !reconfigure) {
            startPlayer()
            return
        }
        b = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.serverInput.setText(config.serverUrl.orEmpty())
        b.homeCheck.isChecked = config.homeMode
        b.deviceInfo.text = getString(R.string.device_info, BuildConfig.VERSION_NAME, config.clientId)
        b.serverInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) = onAddressEdited()
        })
        b.serverInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_GO || actionId == EditorInfo.IME_ACTION_DONE) {
                runTest(); true
            } else false
        }
        b.testButton.setOnClickListener { runTest() }
        b.trustCheck.setOnCheckedChangeListener { _, checked ->
            tested = null
            if (checked) runTest()
        }
        b.saveButton.setOnClickListener { save() }
        b.forgetButton.setOnClickListener { forget() }
        b.removeOwnerButton.setOnClickListener { removeOwner() }
        buildSettingsButtons()
        b.endUnlockButton.setOnClickListener { endUnlockWindow() }
        val owner = LockTask.isDeviceOwner(this)
        b.ownerCard.visibility = if (owner) View.VISIBLE else View.GONE
        b.factoryResetButton.visibility = if (FactoryReset.offered(owner)) View.VISIBLE else View.GONE
        b.factoryResetButton.setOnClickListener { confirmFactoryReset() }
        SetupFlags.log(this, "setup screen")
        if (owner) SetupFlags.tryMarkComplete(this)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (config.isConfigured) startPlayer() else finish()
            }
        })
        onAddressEdited()
        b.serverInput.requestFocus()
    }

    override fun onResume() {
        super.onResume()
        if (!::b.isInitialized) return
        LocalUnlockWindow.setupInFront = true // never engage the lockdown under this screen
        tickWindow()
    }

    override fun onPause() {
        LocalUnlockWindow.setupInFront = false
        if (::b.isInitialized) b.root.removeCallbacks(tick)
        super.onPause()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun currentUrl(): String? = ServerUrl.normalize(b.serverInput.text?.toString())

    private fun onAddressEdited() {
        tested = null
        val url = currentUrl()
        if (captured != null && (url == null || ServerUrl.hostOf(url) != captured?.host)) {
            captured = null
            b.trustCard.visibility = View.GONE
            b.trustCheck.isChecked = false
        }
        val warn = url != null && url.startsWith("http://") && !ServerUrl.isPrivateHost(ServerUrl.hostOf(url))
        b.httpWarning.visibility = if (warn) View.VISIBLE else View.GONE
    }

    /** The pin to test with: a freshly trusted certificate, or the saved one for this server. */
    private fun candidatePin(url: String): String? {
        val cert = captured
        if (cert != null && b.trustCheck.isChecked && ServerUrl.hostOf(url) == cert.host) return cert.sha256
        return if (url == config.serverUrl) config.certPin else null
    }

    private fun runTest() {
        val url = currentUrl()
        if (url == null) {
            showResult(getString(R.string.result_invalid), R.color.error)
            return
        }
        val pin = candidatePin(url)
        tested = null
        showResult(getString(R.string.testing), R.color.setup_muted)
        testJob?.cancel()
        testJob = scope.launch {
            val health = withContext(Dispatchers.IO) { Http.health(Http.build(pin), url) }
            if (currentUrl() != url) return@launch
            when (health) {
                is Http.Health.Ok -> {
                    tested = url to pin
                    showResult(getString(R.string.result_ok), R.color.ok)
                }
                is Http.Health.NotAiSignX -> showResult(getString(R.string.result_not_aisignx), R.color.error)
                is Http.Health.Unreachable -> showResult(getString(R.string.result_unreachable, health.reason), R.color.error)
                is Http.Health.Untrusted -> onUntrusted(url, pin, health.reason)
            }
        }
    }

    private suspend fun onUntrusted(url: String, pin: String?, reason: String) {
        val cert = withContext(Dispatchers.IO) { Tls.capture(url) }
        if (currentUrl() != url) return
        if (cert == null) {
            showResult(getString(R.string.result_unreachable, reason), R.color.error)
            return
        }
        val changed = pin != null && pin != cert.sha256
        if (captured?.sha256 != cert.sha256) b.trustCheck.isChecked = false
        captured = cert
        b.trustFingerprint.text = Tls.colonHex(cert.sha256).chunked(48).joinToString("\n")
        b.trustDetails.text = getString(R.string.trust_details, cert.host, cert.subject, cert.validTo)
        b.trustCard.visibility = View.VISIBLE
        showResult(getString(if (changed) R.string.result_pin_mismatch else R.string.result_untrusted), R.color.warn)
        FileLog.i(TAG, "untrusted certificate for ${cert.host}: $reason")
    }

    private fun showResult(text: String, colorRes: Int) {
        b.resultText.text = text
        b.resultText.setTextColor(ContextCompat.getColor(this, colorRes))
    }

    private fun save() {
        val url = currentUrl()
        val ok = tested
        if (url == null || ok == null || ok.first != url) {
            showResult(getString(R.string.need_test), R.color.warn)
            return
        }
        val app = AiSignXApp.from(this)
        if (config.serverUrl != url) {
            // A different server: the old origin's cookie, storage and caches must go.
            app.clearServerData()
        }
        config.saveServer(url, ok.second)
        applyHomeMode(b.homeCheck.isChecked)
        FileLog.i(TAG, "saved server ${ServerUrl.hostOf(url)} (pinned=${ok.second != null})")
        startPlayer()
    }

    private fun forget() {
        AiSignXApp.from(this).clearServerData()
        config.forgetServer()
        tested = null
        captured = null
        b.trustCheck.isChecked = false
        b.trustCard.visibility = View.GONE
        b.serverInput.setText("")
        showResult(getString(R.string.forgotten), R.color.warn)
        FileLog.i(TAG, "server forgotten")
    }

    private val tick = Runnable { tickWindow() }

    /** Shows how long the local unlock window still runs (hidden when it is over or never opened). */
    private fun tickWindow() {
        val left = LocalUnlockWindow.remainingMs(this)
        b.unlockCard.visibility = if (left > 0) View.VISIBLE else View.GONE
        if (left > 0) {
            b.unlockText.text = getString(R.string.unlock_window, LocalUnlock.minutesLeft(left))
            b.root.postDelayed(tick, 5_000L)
        }
    }

    private fun endUnlockWindow() {
        LocalUnlockWindow.end(this)
        startPlayer() // the player engages the lockdown again as soon as it is in front
    }

    private fun buildSettingsButtons() {
        val labels = mapOf(
            "wifi" to R.string.page_wifi, "display" to R.string.page_display, "date" to R.string.page_date,
            "sound" to R.string.page_sound, "apps" to R.string.page_apps, "about" to R.string.page_about,
            "main" to R.string.btn_android_settings,
        )
        for (page in SettingsPages.ALL) {
            val button = Button(this, null, android.R.attr.buttonBarButtonStyle).apply {
                setText(labels.getValue(page.id))
                setBackgroundResource(R.drawable.field_bg)
                setTextColor(ContextCompat.getColor(this@SetupActivity, R.color.accent))
                minimumHeight = (48 * resources.displayMetrics.density).toInt()
                setOnClickListener { openPage(page) }
            }
            val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            lp.topMargin = (8 * resources.displayMetrics.density).toInt()
            b.settingsPages.addView(button, lp)
        }
    }

    /** The setup screen is only reachable after the unlock, so Android's own settings are fair game here. */
    private fun openPage(page: SettingsPages.Page) {
        val result = SettingsPages.open(page) { action ->
            try {
                startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                true
            } catch (e: Exception) {
                FileLog.w(TAG, "could not open $action: ${e.javaClass.simpleName}")
                false
            }
        }
        when (result) {
            SettingsPages.Result.OPENED -> Unit
            SettingsPages.Result.FALLBACK -> showResult(getString(R.string.page_fallback), R.color.warn)
            SettingsPages.Result.FAILED -> showResult(getString(R.string.page_failed), R.color.error)
        }
    }

    /** Two steps: a warning, then the word RESET typed. Device Owner only (the button is hidden otherwise). */
    private fun confirmFactoryReset() {
        if (!FactoryReset.offered(LockTask.isDeviceOwner(this))) return
        AlertDialog.Builder(this)
            .setTitle(R.string.reset_title)
            .setMessage(R.string.reset_message)
            .setNegativeButton(R.string.reset_cancel, null)
            .setPositiveButton(R.string.reset_continue) { _, _ -> askResetWord() }
            .show()
    }

    private fun askResetWord() {
        val field = EditText(this).apply {
            setHint(R.string.reset_type_hint)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.reset_type_title)
            .setView(field)
            .setNegativeButton(R.string.reset_cancel, null)
            .setPositiveButton(R.string.reset_erase) { _, _ ->
                if (!FactoryReset.confirmed(field.text.toString())) {
                    showResult(getString(R.string.reset_wrong), R.color.warn)
                } else if (!LockTask.wipeDevice(this, config.homeMode)) {
                    showResult(getString(R.string.reset_failed), R.color.error)
                }
            }
            .show()
    }

    private fun applyHomeMode(enabled: Boolean) {
        // Same code path as the set_startup command (mode shell/app). As Device Owner with Lock player on,
        // the alias stays enabled as the persistent Home; the player sets it again on its next start.
        HomeMode.apply(this, enabled)
    }

    /** Reached only through the PIN-gated exit, so a viewer cannot do this. */
    private fun removeOwner() {
        val ok = LockTask.removeOwner(this, config.homeMode)
        if (ok) b.ownerCard.visibility = View.GONE
        showResult(getString(if (ok) R.string.owner_removed else R.string.owner_remove_failed), if (ok) R.color.ok else R.color.error)
    }

    private fun startPlayer() {
        startActivity(Intent(this, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finish()
    }

    companion object {
        private const val TAG = "Setup"
        const val EXTRA_RECONFIGURE = "com.aisignx.player.RECONFIGURE"
    }
}
