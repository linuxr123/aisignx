package com.aisignx.player.shell

import org.json.JSONObject
import java.security.MessageDigest
import java.util.Base64

/**
 * The checklist for signed (Tier B) device commands, as plain Kotlin without Android calls
 * (JVM unit tests). Protocol: docs/signed-commands.md. The caller (Agent) does the I/O around it.
 *
 * Only the verified payload is trusted: the unsigned `params` of the command are never read.
 * Nothing taken from the server is run as a command line, a path or a URL; every value is typed.
 */
object SignedCommand {
    const val REBOOT = "reboot_device"
    const val SET_STARTUP = "set_startup"
    const val UPDATE = "update_app"
    val TIER_B = setOf(REBOOT, SET_STARTUP, UPDATE)

    /** Failure reasons the server knows. */
    const val BAD_SIGNATURE = "bad_signature"
    const val WRONG_DISPLAY = "wrong_display"
    const val EXPIRED = "expired"
    const val REPLAYED = "replayed"
    const val NOT_ALLOWED = "not_allowed"
    const val UNSUPPORTED = "unsupported"
    const val BAD_PARAMS = "bad_params"

    /** What this device can do at all (the admin's switches are in [AgentSettings]). */
    data class Caps(val canReboot: Boolean, val canUpdate: Boolean, val canSetStartup: Boolean)

    /**
     * Everything the checklist needs from the device. [pinnedKey] is the key pinned for this server
     * (null: none yet); [keyChanged] is true when the server later presented a different one.
     * [serverTimeMs] comes from the same response as the command: device clocks are often wrong.
     */
    class Env(
        val pinnedKey: ByteArray?,
        val keyChanged: Boolean,
        val displayId: Long?,
        val serverTimeMs: Long?,
        val lastSeq: Long,
        val settings: AgentSettings,
        val caps: Caps,
    )

    sealed class Action {
        object Reboot : Action()
        data class Startup(val autostart: Boolean?, val mode: String?) : Action()
        data class Update(val version: String, val sha256: String, val size: Long) : Action()
    }

    sealed class Result {
        /** Run [action]; the caller stores [seq] BEFORE acting. */
        data class Accept(val id: String, val seq: Long, val action: Action) : Result()
        /** Acknowledge "failed" with [reason]; nothing runs. */
        data class Reject(val id: String, val reason: String) : Result()
        /** Malformed or already handled: do nothing at all (a bad id cannot even be acked). */
        object Ignore : Result()
    }

    /** True when the command object names a Tier B action in its unsigned field. */
    fun isTierB(c: Any?): Boolean {
        val action = (c as? JSONObject)?.opt("action") as? String ?: return false
        return action in TIER_B
    }

    private val VERSION_RE = Regex("^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
    private val SHA_RE = Regex("^[0-9a-f]{64}$")
    const val MAX_PACKAGE_BYTES = 500L * 1024 * 1024

    fun verify(
        c: Any?,
        env: Env,
        handled: Set<String>,
        check: (key: ByteArray, message: ByteArray, sig: ByteArray) -> Boolean = Ed25519::verify,
    ): Result {
        val o = c as? JSONObject ?: return Result.Ignore
        val id = o.opt("id") as? String ?: return Result.Ignore
        if (!AgentCommands.validId(id) || id in handled) return Result.Ignore
        fun fail(reason: String) = Result.Reject(id, reason)

        // 1. The signature, over the exact payload text, with the pinned key.
        val signed = o.optJSONObject("signed") ?: return fail(BAD_SIGNATURE)
        val payloadText = signed.opt("payload") as? String ?: return fail(BAD_SIGNATURE)
        val sig = decode(signed.opt("sig") as? String) ?: return fail(BAD_SIGNATURE)
        val key = env.pinnedKey
        if (env.keyChanged || key == null) return fail(BAD_SIGNATURE)
        if (!check(key, payloadText.toByteArray(Charsets.UTF_8), sig)) return fail(BAD_SIGNATURE)

        // 2. The payload: version, this display, and the same command as the unsigned fields say.
        val p = try { JSONObject(payloadText) } catch (_: Exception) { return fail(BAD_SIGNATURE) }
        if (integer(p.opt("v")) != 1L) return fail(BAD_SIGNATURE)
        val display = integer(p.opt("display_id"))
        if (display == null || env.displayId == null || display != env.displayId) return fail(WRONG_DISPLAY)
        val action = p.opt("action") as? String ?: return fail(BAD_SIGNATURE)
        if (p.opt("request_id") != id || action != o.opt("action")) return fail(BAD_SIGNATURE)

        // 3. Not expired, by the server's clock.
        val exp = integer(p.opt("exp_ms")) ?: return fail(BAD_SIGNATURE)
        val now = env.serverTimeMs ?: return fail(EXPIRED)
        if (exp <= now) return fail(EXPIRED)

        // 4. A sequence number never seen before (the caller stores it before acting).
        val seq = integer(p.opt("seq")) ?: return fail(BAD_SIGNATURE)
        if (seq <= env.lastSeq) return fail(REPLAYED)

        // 5. Supported here, typed params from the signed payload, and switched on by the admin.
        val params = p.optJSONObject("params") ?: return fail(BAD_PARAMS)
        return when (action) {
            REBOOT -> when {
                !env.caps.canReboot -> fail(UNSUPPORTED)
                params.length() != 0 -> fail(BAD_PARAMS)
                !env.settings.reboot -> fail(NOT_ALLOWED)
                else -> Result.Accept(id, seq, Action.Reboot)
            }
            SET_STARTUP -> {
                val a = startupParams(params) ?: return fail(BAD_PARAMS)
                when {
                    !env.caps.canSetStartup -> fail(UNSUPPORTED)
                    !env.settings.startup -> fail(NOT_ALLOWED)
                    else -> Result.Accept(id, seq, a)
                }
            }
            UPDATE -> {
                val a = updateParams(params)
                when {
                    !env.caps.canUpdate -> fail(UNSUPPORTED)
                    a == null -> fail(BAD_PARAMS)
                    !env.settings.update -> fail(NOT_ALLOWED)
                    else -> Result.Accept(id, seq, a)
                }
            }
            else -> fail(UNSUPPORTED)
        }
    }

    /** {"autostart": bool, "mode": "app"|"shell"}, both optional, nothing else. Null: bad params. */
    fun startupParams(p: JSONObject): Action.Startup? {
        val keys = p.keys().asSequence().toSet()
        if (!setOf("autostart", "mode").containsAll(keys)) return null
        val auto = if (p.has("autostart")) (p.opt("autostart") as? Boolean ?: return null) else null
        val mode = if (p.has("mode")) (p.opt("mode") as? String ?: return null) else null
        if (mode != null && mode != "app" && mode != "shell") return null
        return Action.Startup(auto, mode)
    }

    /** {"version", "sha256", "size"}, nothing else. Null: bad params. */
    fun updateParams(p: JSONObject): Action.Update? {
        if (p.length() != 3) return null
        val version = p.opt("version") as? String ?: return null
        val sha = (p.opt("sha256") as? String)?.lowercase() ?: return null
        val size = integer(p.opt("size")) ?: return null
        if (!VERSION_RE.matches(version) || !SHA_RE.matches(sha) || size !in 1..MAX_PACKAGE_BYTES) return null
        return Action.Update(version, sha, size)
    }

    /** A JSON integer (not a float, string or boolean), or null. */
    fun integer(v: Any?): Long? = when (v) {
        is Int -> v.toLong()
        is Long -> v
        else -> null
    }

    private fun decode(b64: String?): ByteArray? {
        if (b64 == null) return null
        return try { Base64.getDecoder().decode(b64) } catch (_: IllegalArgumentException) { null }
    }
}

/** Trust on first use for the server's command key, per server origin. */
object KeyPin {
    sealed class Decision {
        /** No pin yet: pin [key]. */
        data class Pin(val key: ByteArray) : Decision()
        /** The server still presents the pinned key. */
        object Same : Decision()
        /** The server presents a different key: keep the old pin, refuse Tier B, report it. */
        object Changed : Decision()
        /** The answer was unusable: change nothing. */
        object Unusable : Decision()
    }

    /** The 32 raw bytes out of GET /command-key's JSON, or null. */
    fun parseKey(body: String): ByteArray? = try {
        val text = JSONObject(body).opt("public_key") as? String
        text?.let { Base64.getDecoder().decode(it) }?.takeIf { it.size == Ed25519.KEY_BYTES }
    } catch (_: Exception) { null }

    fun decide(pinned: ByteArray?, fetched: ByteArray?): Decision = when {
        fetched == null -> Decision.Unusable
        pinned == null -> Decision.Pin(fetched)
        MessageDigest.isEqual(pinned, fetched) -> Decision.Same
        else -> Decision.Changed
    }

    /** First 16 hex characters of the SHA-256 of the raw key, in groups of four (as the server shows it). */
    fun fingerprint(key: ByteArray): String {
        val hex = MessageDigest.getInstance("SHA-256").digest(key).joinToString("") { "%02x".format(it) }.take(16)
        return hex.chunked(4).joinToString(" ")
    }
}

/** Device-side limits and verification of Tier B actions. */
object TierRules {
    const val REBOOT_COOLDOWN_MS = 10 * 60_000L

    /** Preference keys dropped by "Forget server" / re-pair: pin, key-changed flag, sequence number, server-time mark. */
    fun forgottenOnRepair(key: String): Boolean = listOf("pin_", "kc_", "seq_", "st_").any { key.startsWith(it) }

    /** [ms] (a response's server_time_ms) is not lower than the highest one seen before. */
    fun serverTimeOk(highWater: Long, ms: Long): Boolean = ms >= highWater

    /** The "last_update_error" text: "<version>: <reason>", short and without secrets. */
    fun updateErrorText(version: String, reason: String): String = "$version: $reason".replace('|', ' ').take(100)

    /** An update error is shown until the app has the version it failed for, or for a day. */
    fun showUpdateError(stored: String?, installedVersion: String, nowMs: Long): String? {
        val parts = stored?.split('|') ?: return null
        if (parts.size != 2) return null
        val at = parts[1].toLongOrNull() ?: return null
        val target = parts[0].substringBefore(":")
        if (target == installedVersion || nowMs - at > 24 * 3_600_000L || at > nowMs + 3_600_000L) return null
        return parts[0]
    }

    fun storeUpdateError(version: String, reason: String, nowMs: Long): String = updateErrorText(version, reason) + "|" + nowMs

    /**
     * At most one reboot per 10 minutes (wall clock, persisted). A last reboot far in the future means
     * the clock was set back, which does not block forever.
     */
    fun rebootAllowed(lastMs: Long, nowMs: Long): Boolean =
        lastMs <= 0L || nowMs - lastMs >= REBOOT_COOLDOWN_MS || lastMs - nowMs > REBOOT_COOLDOWN_MS

    /** Whether the installed version already is the requested one (no install, no restart loop). */
    fun alreadyInstalled(installed: String, requested: String): Boolean = installed == requested

    /** Room needed to download a package of [size] bytes and then hand it to the installer. */
    fun enoughSpace(usable: Long, size: Long): Boolean = usable >= size + size / 2 + 32L * 1024 * 1024

    /** Should the app start after an update replaced it (and after a boot)? */
    fun startAfterUpdate(configured: Boolean, homeMode: Boolean, kioskLock: Boolean, updatePending: Boolean): Boolean =
        configured && (homeMode || kioskLock || updatePending)

    fun startAtBoot(configured: Boolean, autostart: Boolean): Boolean = configured && autostart
}

/**
 * Checks a package while it streams to disk: never more than the signed size, exactly that size at
 * the end, and the signed SHA-256.
 */
class PackageVerifier(private val expectedSize: Long, expectedSha256: String) {
    private val sha = MessageDigest.getInstance("SHA-256")
    private val want = expectedSha256.lowercase()
    var received = 0L
        private set

    /** False as soon as more than the signed size arrives (stop downloading). */
    fun update(buf: ByteArray, len: Int): Boolean {
        received += len
        if (received > expectedSize) return false
        sha.update(buf, 0, len)
        return true
    }

    enum class Verdict { OK, BAD_SIZE, BAD_HASH }

    fun finish(): Verdict {
        if (received != expectedSize) return Verdict.BAD_SIZE
        val got = sha.digest().joinToString("") { "%02x".format(it) }
        return if (got == want) Verdict.OK else Verdict.BAD_HASH
    }
}
