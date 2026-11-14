package com.aisignx.player

import android.app.admin.DeviceAdminReceiver

/**
 * Lets AiSignX Player be provisioned as Android Device Owner, for a full Lock player
 * lockdown (lock task without the pin prompt, no Home, Recents or status bar):
 *   adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
 *
 * Same class name as v2, so devices provisioned with v2 keep their owner component.
 * See docs/kiosk-lockdown.md.
 */
class AisignxDeviceAdminReceiver : DeviceAdminReceiver()
