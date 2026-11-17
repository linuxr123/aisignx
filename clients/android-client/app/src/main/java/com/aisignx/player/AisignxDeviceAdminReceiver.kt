package com.aisignx.player

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent
import com.aisignx.player.provision.Provisioning
import com.aisignx.player.ui.LockTask
import com.aisignx.player.ui.LockdownApplier

/**
 * Lets AiSignX Player be Android Device Owner, for a full Lock player lockdown (lock task without
 * the pin prompt, no Home, Recents or status bar):
 *  - from a QR code on a factory-reset tablet (docs/android-kiosk.md), or
 *  - with adb: adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
 *
 * Same class name as v2, so devices provisioned with v2 keep their owner component.
 * See docs/kiosk-lockdown.md.
 */
class AisignxDeviceAdminReceiver : DeviceAdminReceiver() {

    /** QR provisioning finished: keep the server and pairing secret, then claim the pairing. */
    override fun onProfileProvisioningComplete(context: Context, intent: Intent) {
        if (!LockTask.isDeviceOwner(context)) return // only a fully managed device is supported
        if (Provisioning.acceptFromIntent(context, intent)) Provisioning.kick(context)
        LockdownApplier.syncStored(context)
    }
}
