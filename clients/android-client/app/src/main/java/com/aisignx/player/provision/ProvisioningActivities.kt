package com.aisignx.player.provision

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.Intent
import android.os.Build
import android.os.Bundle
import com.aisignx.player.FileLog

/**
 * Android 12+ asks the device policy controller how it wants to be provisioned. This app is only ever
 * a fully managed device (a signage tablet); if that is not on offer, the answer is "cancel".
 * No screen: it answers and finishes.
 */
class GetProvisioningModeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val allowed = intent.getIntegerArrayListExtra(DevicePolicyManager.EXTRA_PROVISIONING_ALLOWED_PROVISIONING_MODES)
        val mode = ProvisionRules.chooseMode(allowed)
        if (mode == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            FileLog.w("Provision", "fully managed mode is not offered; cancelling")
            setResult(RESULT_CANCELED)
        } else {
            setResult(RESULT_OK, Intent().putExtra(DevicePolicyManager.EXTRA_PROVISIONING_MODE, mode))
        }
        finish()
    }
}

/**
 * Android 9+ ends provisioning by asking the controller whether the device complies with its policy.
 * There is nothing for a person to do here: keep the extras (in case the receiver did not get them) and
 * answer OK at once; the pairing is claimed by [ProvisionActivity] without a screen tap.
 */
class PolicyComplianceActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Provisioning.acceptFromIntent(this, intent)) Provisioning.kick(this)
        setResult(RESULT_OK)
        finish()
    }
}
