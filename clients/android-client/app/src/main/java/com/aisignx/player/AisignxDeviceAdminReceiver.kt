package com.aisignx.player

import android.app.admin.DeviceAdminReceiver

/**
 * Required so AISignX Player can be provisioned as Android Device Owner.
 *
 * Device Owner is the Android-supported path for unattended kiosk updates:
 * PackageInstaller can approve the app's own update session without the
 * operator tapping the system "Install" prompt.
 */
class AisignxDeviceAdminReceiver : DeviceAdminReceiver()
