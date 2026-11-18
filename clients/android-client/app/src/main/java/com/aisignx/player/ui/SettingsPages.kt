package com.aisignx.player.ui

import android.provider.Settings

/**
 * The Android settings pages the Setup screen offers (Device Owner or not, after the PIN unlock).
 * Pure: the caller supplies what "open" means, so the order and fallbacks can be unit tested.
 * Some builds (Samsung after QR provisioning) close the main Settings page by themselves, so the
 * direct pages come first and the main page is only the last entry of the list.
 */
object SettingsPages {

    class Page(val id: String, val actions: List<String>)

    val ALL: List<Page> = listOf(
        Page("wifi", listOf(Settings.ACTION_WIFI_SETTINGS)),
        Page("display", listOf(Settings.ACTION_DISPLAY_SETTINGS)),
        Page("date", listOf(Settings.ACTION_DATE_SETTINGS)),
        Page("sound", listOf(Settings.ACTION_SOUND_SETTINGS)),
        Page("apps", listOf(Settings.ACTION_APPLICATION_SETTINGS, Settings.ACTION_MANAGE_ALL_APPLICATIONS_SETTINGS)),
        Page("about", listOf(Settings.ACTION_DEVICE_INFO_SETTINGS)),
        Page("main", listOf(Settings.ACTION_SETTINGS)),
    )

    /** The main page, the fallback for every other page. */
    const val MAIN_ACTION = Settings.ACTION_SETTINGS

    enum class Result { OPENED, FALLBACK, FAILED }

    /**
     * Tries the page's own actions in order; when none opens, the main Settings page (unless this is
     * the main page). [tryOpen] returns false or throws when an action could not be started.
     */
    fun open(page: Page, tryOpen: (String) -> Boolean): Result {
        for (action in page.actions) if (attempt(action, tryOpen)) return Result.OPENED
        if (MAIN_ACTION !in page.actions && attempt(MAIN_ACTION, tryOpen)) return Result.FALLBACK
        return Result.FAILED
    }

    private fun attempt(action: String, tryOpen: (String) -> Boolean): Boolean =
        try { tryOpen(action) } catch (_: Exception) { false }
}
