package com.aisignx.player.ui

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import com.aisignx.player.ApiClient
import com.aisignx.player.Config
import com.aisignx.player.R
import com.aisignx.player.SignageApp
import com.aisignx.player.databinding.ActivitySetupBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class SetupActivity : AppCompatActivity() {

    private lateinit var b: ActivitySetupBinding

    companion object {
        const val EXTRA_CONFIG_TEXT = "com.aisignx.player.EXTRA_CONFIG_TEXT"
    }

    private val importPlayerConfig = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) applyImportedPlayerConfig(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val importedConfigText = intent.getStringExtra(EXTRA_CONFIG_TEXT)

        // If already fully configured, skip straight to player unless we were
        // explicitly opened to import a replacement setup file.
        if (Config.isConfigured && importedConfigText == null) {
            startPlayer(); return
        }
        // If server saved but no token, go to waiting unless an import was requested.
        if (Config.hasServer && importedConfigText == null) {
            startActivity(Intent(this, WaitingActivity::class.java))
            finish(); return
        }

        b = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(b.root)
        hideSystemUi()      // must come AFTER setContentView -- decor view
                            // doesn't exist before that on Android 14+ (Samsung)

        val updateLabels = arrayOf(
            "Automatic (install when available)",
            "Prompt before installing",
            "Manual only (admin commands)"
        )
        val updateValues = arrayOf(
            SignageApp.UPDATE_MODE_AUTO,
            SignageApp.UPDATE_MODE_PROMPT,
            SignageApp.UPDATE_MODE_MANUAL
        )
        b.spinnerUpdateMode.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            updateLabels
        )
        val currentMode = Config.updateMode
        val selectedIdx = updateValues.indexOf(currentMode).takeIf { it >= 0 } ?: 1
        b.spinnerUpdateMode.setSelection(selectedIdx)

        showStep(1)

        b.btnConnect.setOnClickListener { onConnectClicked() }
        b.btnRegister.setOnClickListener { onRegisterClicked() }
        b.btnImportConfig.setOnClickListener {
            importPlayerConfig.launch("*/*")
        }

        if (importedConfigText != null) {
            applyImportedPlayerConfigText(importedConfigText)
        }
    }

    private fun showStep(step: Int) {
        b.step1.visibility = if (step == 1) View.VISIBLE else View.GONE
        b.step2.visibility = if (step == 2) View.VISIBLE else View.GONE
    }

    private fun applyImportedPlayerConfig(uri: Uri) {
        try {
            val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
                ?: run { toast("Could not read file"); return }
            applyImportedPlayerConfigText(text)
        } catch (e: Exception) {
            toast("Invalid file: ${e.message ?: "parse error"}")
        }
    }

    private fun applyImportedPlayerConfigText(text: String) {
        try {
            val obj = JSONObject(text)
            if (obj.optString("format") != "aisignx-player-config") {
                toast("Not an AISignX player config file")
                return
            }
            val server = obj.optString("server_url").trim().trimEnd('/')
            val token = obj.optString("display_token").ifEmpty { obj.optString("token") }
            val enroll = obj.optString("enrollment_code").trim().replace("-", "").uppercase()
            val um = obj.optString("update_mode").trim().lowercase()
            if (server.isEmpty()) {
                toast("Config missing server_url")
                return
            }
            if (um == "auto" || um == "automatic") {
                Config.updateMode = SignageApp.UPDATE_MODE_AUTO
            } else if (um == "manual") {
                Config.updateMode = SignageApp.UPDATE_MODE_MANUAL
            } else if (um.isNotEmpty()) {
                Config.updateMode = SignageApp.UPDATE_MODE_PROMPT
            }
            if (token.isNotEmpty()) {
                applyImportedToken(server, token)
                return
            }
            if (enroll.isNotEmpty()) {
                Config.serverUrl = server
                b.etServerUrl.setText(server)
                b.etEnrollmentCode.setText(enroll)
                val dn = obj.optString("display_name").trim()
                b.etFriendlyName.setText(dn.ifEmpty { Build.MODEL })
                val updateValues = arrayOf(
                    SignageApp.UPDATE_MODE_AUTO,
                    SignageApp.UPDATE_MODE_PROMPT,
                    SignageApp.UPDATE_MODE_MANUAL
                )
                val idx = updateValues.indexOf(Config.updateMode).takeIf { it >= 0 } ?: 1
                b.spinnerUpdateMode.setSelection(idx)
                showStep(2)
                toast("Loaded enrollment — tap Request Registration when ready")
                return
            }
            toast("Config needs display_token or enrollment_code")
        } catch (e: Exception) {
            toast("Invalid file: ${e.message ?: "parse error"}")
        }
    }

    private fun applyImportedToken(server: String, token: String) {
        b.etServerUrl.setText(server)
        setLoading(true, "Checking setup file server...")
        lifecycleScope.launch(Dispatchers.IO) {
            val ok = ApiClient.ping(server)
            withContext(Dispatchers.Main) {
                setLoading(false)
                if (!ok) {
                    Config.clear()
                    showStep(1)
                    toast("Setup file loaded, but this Android device cannot reach: $server")
                    return@withContext
                }
                Config.serverUrl = server
                Config.token = token
                toast("Loaded display token - starting player")
                startPlayer()
            }
        }
    }

    private fun onConnectClicked() {
        val url = b.etServerUrl.text.toString().trim().trimEnd('/')
        if (url.isEmpty()) { toast("Please enter a server URL"); return }
        setLoading(true, "Connecting…")
        lifecycleScope.launch(Dispatchers.IO) {
            val ok = ApiClient.ping(url)
            withContext(Dispatchers.Main) {
                setLoading(false)
                if (ok) {
                    Config.serverUrl = url
                    b.etFriendlyName.setText(Build.MODEL)
                    b.etEnrollmentCode.setText("")
                    showStep(2)
                } else {
                    toast("Cannot reach server. Check URL and network.")
                }
            }
        }
    }

    private fun onRegisterClicked() {
        val name = b.etFriendlyName.text.toString().trim().ifEmpty { Build.MODEL }
        val code = b.etEnrollmentCode.text.toString().trim().uppercase()
        if (code.isEmpty()) {
            toast("Please enter the enrollment code provided by your administrator")
            return
        }
        val updateValues = arrayOf(
            SignageApp.UPDATE_MODE_AUTO,
            SignageApp.UPDATE_MODE_PROMPT,
            SignageApp.UPDATE_MODE_MANUAL
        )
        Config.updateMode = updateValues.getOrElse(b.spinnerUpdateMode.selectedItemPosition) {
            SignageApp.UPDATE_MODE_PROMPT
        }
        setLoading(true, "Sending request…")
        lifecycleScope.launch(Dispatchers.IO) {
            val result = ApiClient.register(
                serverUrl       = Config.serverUrl,
                deviceId        = Config.deviceId,
                friendlyName    = name,
                hostname        = Build.MODEL,
                os              = "Android ${Build.VERSION.RELEASE}",
                resolution      = getDisplayResolution(),
                appVersion      = packageManager.getPackageInfo(packageName, 0).versionName ?: "1.0",
                enrollmentCode  = code
            )
            withContext(Dispatchers.Main) {
                setLoading(false)
                when (result) {
                    is ApiClient.RegisterResult.Approved -> {
                        Config.token = result.token
                        startPlayer()
                    }
                    ApiClient.RegisterResult.Pending -> {
                        startActivity(Intent(this@SetupActivity, WaitingActivity::class.java))
                        finish()
                    }
                    is ApiClient.RegisterResult.Error -> toast("Error: ${result.message}")
                }
            }
        }
    }

    /**
     * Returns the display resolution as "WxH". Uses the modern API on
     * Android 11+ and falls back to DisplayMetrics on older devices
     * (e.g. Sony Bravia Android TVs running Android 8/9/10) which don't
     * have WindowManager.currentWindowMetrics.
     */
    private fun getDisplayResolution(): String = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val r = windowManager.currentWindowMetrics.bounds
            "${r.width()}x${r.height()}"
        } else {
            @Suppress("DEPRECATION")
            val dm = android.util.DisplayMetrics().also {
                @Suppress("DEPRECATION")
                windowManager.defaultDisplay.getRealMetrics(it)
            }
            "${dm.widthPixels}x${dm.heightPixels}"
        }
    } catch (_: Exception) { "unknown" }

    private fun setLoading(loading: Boolean, msg: String = "") {
        b.btnConnect.isEnabled = !loading
        b.btnRegister.isEnabled = !loading
        b.btnImportConfig.isEnabled = !loading
        b.tvStatus.text = if (loading) msg else ""
        b.tvStatus.visibility = if (loading) View.VISIBLE else View.GONE
    }

    private fun startPlayer() {
        startActivity(Intent(this, PlayerActivity::class.java))
        finish()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

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
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
}
