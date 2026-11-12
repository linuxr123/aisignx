package com.aisignx.player

import android.app.Application
import android.content.Context
import android.webkit.CookieManager
import android.webkit.WebStorage
import com.aisignx.player.cache.MediaCache
import com.aisignx.player.cache.MediaDownloader
import com.aisignx.player.cache.PageCache
import com.aisignx.player.net.Http
import java.io.File

class AiSignXApp : Application() {

    lateinit var config: Config
        private set

    /** Caches and downloader for the configured server; rebuilt when it or its pin changes. */
    class Services(
        val origin: String,
        val pin: String?,
        val pageCache: PageCache,
        val mediaCache: MediaCache,
        val downloader: MediaDownloader,
    )

    private var services: Services? = null

    override fun onCreate() {
        super.onCreate()
        config = Config(this)
        FileLog.init(this, BuildConfig.VERSION_NAME)
        config.ensureClientId()
        if (!config.v2Cleaned) cleanUpV2()
    }

    @Synchronized
    fun services(): Services? {
        val origin = config.serverUrl ?: return null
        val pin = config.certPin
        services?.let { if (it.origin == origin && it.pin == pin) return it }
        services?.downloader?.shutdown()
        val client = Http.client(pin)
        val media = MediaCache(mediaDir(this))
        val downloader = MediaDownloader(media, origin, client, Http::cookieFor)
        media.onMissing = { path, video -> downloader.enqueue(path, video) }
        val s = Services(origin, pin, PageCache(pageDir(this), origin, client), media, downloader)
        services = s
        return s
    }

    /**
     * The server changed or was forgotten: drop cookies (the display credential),
     * web storage, both caches and the running downloader. The client ID stays.
     * Call on the main thread.
     */
    @Synchronized
    fun clearServerData() {
        services?.downloader?.shutdown()
        services = null
        try {
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            WebStorage.getInstance().deleteAllData()
        } catch (e: Exception) {
            FileLog.w(TAG, "clearing web data failed: ${e.message}")
        }
        pageDir(this).deleteRecursively()
        mediaDir(this).deleteRecursively()
        FileLog.i(TAG, "server data cleared")
    }

    /** First v3 start after a v2 install: v2's settings and media cache are not used. */
    private fun cleanUpV2() {
        try {
            deleteSharedPreferences("signage_prefs")
            File(filesDir, "media-cache").deleteRecursively()
        } catch (e: Exception) {
            FileLog.w(TAG, "v2 cleanup: ${e.message}")
        }
        config.v2Cleaned = true
    }

    companion object {
        private const val TAG = "App"

        fun from(ctx: Context): AiSignXApp = ctx.applicationContext as AiSignXApp
        fun pageDir(ctx: Context) = File(ctx.filesDir, "page-cache")
        fun mediaDir(ctx: Context) = File(ctx.filesDir, "media")
    }
}
