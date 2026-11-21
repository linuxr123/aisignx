package com.aisignx.player.provision

import android.content.Context
import android.content.Intent
import android.os.PersistableBundle
import android.app.admin.DevicePolicyManager
import android.os.Build
import android.webkit.CookieManager
import com.aisignx.player.AiSignXApp
import com.aisignx.player.FileLog
import com.aisignx.player.net.Http
import com.aisignx.player.shell.AgentStore
import com.aisignx.player.shell.KeyPin
import com.aisignx.player.ui.LockTask
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import javax.net.ssl.SSLException

/**
 * QR provisioning, Android part: keep the extras, claim the pre-approved pairing, finish.
 * The pairing secret lives in private preferences until the claim worked or can never work, and is
 * never logged. Nothing here is reachable from the web page.
 */
object Provisioning {
    private const val TAG = "Provision"
    private const val PREFS = "provision"
    private const val KEY_SECRET = "secret"
    private const val KEY_COOKIES = "claimed_cookies"

    private fun prefs(ctx: Context) = ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** A QR set-up whose pairing has not been claimed yet. */
    fun pending(ctx: Context): Boolean = prefs(ctx).getString(KEY_SECRET, null) != null

    /** Reads the admin extras out of an intent of the provisioning flow (receiver or compliance activity). */
    @Suppress("DEPRECATION")
    fun acceptFromIntent(ctx: Context, intent: Intent?): Boolean {
        val bundle: PersistableBundle? = if (Build.VERSION.SDK_INT >= 33) {
            intent?.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE, PersistableBundle::class.java)
        } else {
            intent?.getParcelableExtra(DevicePolicyManager.EXTRA_PROVISIONING_ADMIN_EXTRAS_BUNDLE)
        }
        return accept(ctx, bundle?.getString("server_url"), bundle?.getString("pairing_secret"), bundle?.getString("command_key"))
    }

    /**
     * Stores the server and the secret. The first set-up wins: once a server is configured or a claim is
     * pending, a repeated delivery of the same extras is ignored. The app becomes the Home app (a managed
     * signage tablet should boot into it); the Lock player switch from the web page decides the rest.
     */
    @Synchronized
    fun accept(ctx: Context, serverUrl: String?, secret: String?, commandKey: String? = null, homeMode: Boolean = true): Boolean {
        val config = AiSignXApp.from(ctx).config
        if (pending(ctx) || config.isConfigured) return false
        val extras = ProvisionRules.parseExtras(serverUrl, secret)
        if (extras == null) {
            FileLog.w(TAG, "provisioning extras missing or invalid")
            return false
        }
        config.saveServer(extras.serverUrl, null)
        // A managed tablet boots into the player (Home app); a phone installed as an app keeps its own launcher.
        config.homeMode = homeMode
        LockTask.trySetHomeAlias(ctx, homeMode)
        // The QR code carries the server's command key: pin it now, so there is no first-contact window.
        extras.commandKey?.let {
            AgentStore(ctx).apply { forgetKeyPins(); pinKey(extras.serverUrl, it) }
            FileLog.i(TAG, "command key ${KeyPin.fingerprint(it)} pinned from the QR code")
        } ?: FileLog.w(TAG, "no usable command key in the QR code; it is pinned on first contact instead")
        prefs(ctx).edit().putString(KEY_SECRET, extras.secret).commit()
        FileLog.i(TAG, "provisioned for ${extras.serverUrl}; claiming the pairing")
        return true
    }

    /** Result of one claim attempt: the outcome and the display cookie headers (never logged). */
    class Attempt(val claim: ProvisionRules.Claim, val cookies: List<String>)

    /** Blocking network call: call off the main thread. */
    fun claim(ctx: Context): Attempt {
        val config = AiSignXApp.from(ctx).config
        val origin = config.serverUrl
        val secret = prefs(ctx).getString(KEY_SECRET, null)
        if (origin == null || secret == null) return Attempt(ProvisionRules.Claim.GIVE_UP, emptyList())
        // A claim that worked before the process died: the server forgets the code after one claim, so the
        // credential kept here is all that is left of it.
        savedCookies(ctx)?.let { return Attempt(ProvisionRules.Claim.PAIRED, it) }
        val body = JSONObject().put("pairing_secret", secret).put("credential", "cookie").toString()
        val req = Request.Builder().url("$origin/api/player/v1/pair/status")
            .header("Accept", "application/json")
            .post(body.toRequestBody("application/json".toMediaType())).build()
        return try {
            val client = Http.client(config.certPin).newBuilder().followRedirects(false).build()
            client.newCall(req).execute().use { r ->
                val text = r.peekBody(16 * 1024).string()
                val c = ProvisionRules.classify(r.code, ProvisionRules.statusOf(text))
                FileLog.i(TAG, "claim: HTTP ${r.code} -> ${c.name.lowercase()}")
                val cookies = if (c == ProvisionRules.Claim.PAIRED) ProvisionRules.displayCookies(r.headers("Set-Cookie")) else emptyList()
                // Durable first: the pairing code is spent now, so the credential must not live only in memory.
                if (cookies.isNotEmpty()) prefs(ctx).edit().putStringSet(KEY_COOKIES, cookies.toSet()).commit()
                Attempt(if (c == ProvisionRules.Claim.PAIRED && cookies.isEmpty()) ProvisionRules.Claim.GIVE_UP else c, cookies)
            }
        } catch (e: SSLException) {
            val c = ProvisionRules.classifyFailure(e)
            if (c == ProvisionRules.Claim.GIVE_UP) FileLog.w(TAG, "claim: the server certificate is not trusted; use setup to trust it")
            else FileLog.i(TAG, "claim: TLS problem, trying again (${e.javaClass.simpleName})")
            Attempt(c, emptyList())
        } catch (e: IOException) {
            FileLog.i(TAG, "claim: no connection yet (${e.javaClass.simpleName})")
            Attempt(ProvisionRules.Claim.RETRY, emptyList())
        }
    }

    private fun savedCookies(ctx: Context): List<String>? =
        prefs(ctx).getStringSet(KEY_COOKIES, null)?.toList()?.takeIf { it.isNotEmpty() }

    /**
     * Main thread: hand the display credential to the WebView, as the page's own pairing would have.
     * True only when the WebView can read it back (then the secret may be cleared).
     */
    fun storeCookies(ctx: Context, cookies: List<String>): Boolean {
        val origin = AiSignXApp.from(ctx).config.serverUrl ?: return false
        val cm = CookieManager.getInstance()
        cm.setAcceptCookie(true)
        // The server scopes this cookie to /api/player, so it is set, and read back, at that address. Reading it at
        // the site root never returns it (a cookie is only sent to its own path) and the read-back failed forever.
        val url = ProvisionRules.cookieUrl(origin)
        for (c in cookies) cm.setCookie(url, c)
        cm.flush()
        // HttpOnly cookies are not visible to page script, but getCookie (native) returns them.
        return cm.getCookie(url).orEmpty().contains("aisignx_display=")
    }

    /** Done (paired, or given up): forget the secret and the kept credential. */
    fun complete(ctx: Context) {
        prefs(ctx).edit().remove(KEY_SECRET).remove(KEY_COOKIES).commit()
    }

    /**
     * Setting up by QR did not work (waited too long, or a person pressed the hidden exit): forget the server
     * and the secret, so the normal setup screen opens and the address can be fixed. Home mode stays.
     * Main thread.
     */
    fun abandon(ctx: Context) {
        complete(ctx)
        val app = AiSignXApp.from(ctx)
        app.clearServerData()
        app.config.forgetServer()
        FileLog.w(TAG, "QR set-up abandoned; normal setup")
    }

    /** Starts the progress screen. Allowed from the receiver because the app is Device Owner. */
    fun kick(ctx: Context) {
        try {
            ctx.startActivity(Intent(ctx, ProvisionActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            FileLog.w(TAG, "could not start the provisioning screen: ${e.javaClass.simpleName}")
        }
    }
}
