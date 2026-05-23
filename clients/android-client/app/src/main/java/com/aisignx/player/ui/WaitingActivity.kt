package com.aisignx.player.ui

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.aisignx.player.ApiClient
import com.aisignx.player.Config
import com.aisignx.player.databinding.ActivityWaitingBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class WaitingActivity : AppCompatActivity() {

    private lateinit var b: ActivityWaitingBinding

    private val importPlayerConfig = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) importSetupFile(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityWaitingBinding.inflate(layoutInflater)
        setContentView(b.root)
        hideSystemUi()      // must come AFTER setContentView -- decor view
                            // doesn't exist before that on Android 14+ (Samsung)
        b.tvDeviceId.text = "Device ID: ${Config.deviceId}"
        b.btnImportConfig.setOnClickListener {
            importPlayerConfig.launch("*/*")
        }
        b.btnReset.setOnClickListener { resetSetup() }
        startPolling()
    }

    private fun startPolling() {
        lifecycleScope.launch(Dispatchers.IO) {
            while (isActive) {
                delay(5_000L)
                val result = ApiClient.pollStatus(Config.serverUrl, Config.deviceId)
                withContext(Dispatchers.Main) {
                    when (result) {
                        is ApiClient.PollResult.Approved -> {
                            Config.token = result.token
                            startActivity(Intent(this@WaitingActivity, PlayerActivity::class.java))
                            finish()
                        }
                        ApiClient.PollResult.Declined -> {
                            b.tvStatus.text = "âŒ Registration Declined"
                            b.tvHint.text = "Contact your administrator, then reset setup to try again."
                            b.tvStatus.visibility = View.VISIBLE
                        }
                        else -> { /* still pending or network error â€” keep polling */ }
                    }
                }
            }
        }
    }

    private fun resetSetup() {
        Config.clear()
        startActivity(Intent(this, SetupActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        })
    }

    private fun importSetupFile(uri: Uri) {
        try {
            val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
                ?: run {
                    Toast.makeText(this, "Could not read setup file", Toast.LENGTH_LONG).show()
                    return
                }
            Config.clear()
            startActivity(Intent(this, SetupActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
                putExtra(SetupActivity.EXTRA_CONFIG_TEXT, text)
            })
            finish()
        } catch (e: Exception) {
            Toast.makeText(this, "Invalid setup file: ${e.message ?: "parse error"}", Toast.LENGTH_LONG).show()
        }
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
