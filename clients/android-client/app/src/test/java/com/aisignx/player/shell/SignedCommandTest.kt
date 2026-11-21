package com.aisignx.player.shell

import com.aisignx.player.shell.SignedCommand.Action
import com.aisignx.player.shell.SignedCommand.Result
import net.i2p.crypto.eddsa.EdDSAEngine
import net.i2p.crypto.eddsa.EdDSAPrivateKey
import net.i2p.crypto.eddsa.spec.EdDSANamedCurveTable
import net.i2p.crypto.eddsa.spec.EdDSAPrivateKeySpec
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest
import java.util.Base64

class SignedCommandTest {

    private val spec = EdDSANamedCurveTable.getByName(EdDSANamedCurveTable.ED_25519)

    /** A key pair made with the same library the app uses. */
    private class Keys(val seed: ByteArray, val pub: ByteArray)

    private fun keys(fill: Int): Keys {
        val seed = ByteArray(32) { (fill + it).toByte() }
        return Keys(seed, EdDSAPrivateKeySpec(seed, spec).a.toByteArray())
    }

    private fun sign(k: Keys, text: String): ByteArray {
        val e = EdDSAEngine(MessageDigest.getInstance("SHA-512"))
        e.initSign(EdDSAPrivateKey(EdDSAPrivateKeySpec(k.seed, spec)))
        e.update(text.toByteArray(Charsets.UTF_8))
        return e.sign()
    }

    private val kp = keys(1)
    private val ID = "0123456789abcdef"
    private val NOW = 1_763_299_000_000L
    private val SHA = "ab".repeat(32)

    private val allOn = AgentSettings(false, false, false, reboot = true, startup = true, update = true)
    private val caps = SignedCommand.Caps(canReboot = true, canUpdate = true, canSetStartup = true)

    private fun env(
        key: ByteArray? = kp.pub, changed: Boolean = false, display: Long? = 7, time: Long? = NOW,
        last: Long = 100, settings: AgentSettings = allOn, c: SignedCommand.Caps = caps,
    ) = SignedCommand.Env(key, changed, display, time, last, settings, c)

    private fun payload(
        action: String = "reboot_device", params: String = "{}", display: Long = 7, id: String = ID,
        seq: Long = 412, exp: Long = NOW + 120_000, v: Int = 1,
    ) = """{"action":"$action","display_id":$display,"exp_ms":$exp,"params":$params,"request_id":"$id","seq":$seq,"v":$v}"""

    /** A command object as GET /commands returns it. [unsignedParams] is what an attacker could vary. */
    private fun cmd(
        payload: String = payload(), action: String = "reboot_device", id: String = ID,
        signer: Keys = kp, sig: String? = null, unsignedParams: String = "{}",
    ): JSONObject {
        val s = sig ?: Base64.getEncoder().encodeToString(sign(signer, payload))
        return JSONObject()
            .put("id", id).put("action", action).put("params", JSONObject(unsignedParams)).put("expires_in_s", 120)
            .put("signed", JSONObject().put("payload", payload).put("sig", s))
    }

    private fun reason(r: Result): String = (r as Result.Reject).reason
    private fun verify(c: Any?, e: SignedCommand.Env = env(), handled: Set<String> = emptySet()) = SignedCommand.verify(c, e, handled)

    // ---- the library and the server's own signatures ---------------------------------------

    @Test fun ed25519AcceptsAGoodSignatureAndRejectsEverythingElse() {
        val sig = sign(kp, "hello")
        assertTrue(Ed25519.verify(kp.pub, "hello".toByteArray(), sig))
        assertFalse(Ed25519.verify(kp.pub, "hellp".toByteArray(), sig))
        val flipped = sig.copyOf().also { it[10] = (it[10].toInt() xor 1).toByte() }
        assertFalse(Ed25519.verify(kp.pub, "hello".toByteArray(), flipped))
        assertFalse(Ed25519.verify(keys(9).pub, "hello".toByteArray(), sig))
        assertFalse(Ed25519.verify(kp.pub.copyOf(31), "hello".toByteArray(), sig))
        assertFalse(Ed25519.verify(kp.pub, "hello".toByteArray(), sig.copyOf(63)))
        assertFalse(Ed25519.verify(ByteArray(32) { 0xff.toByte() }, "hello".toByteArray(), sig))
    }

    /** Made by the server's own code (server/aisignx/displays/signing.py payload_for and sign, seed bytes 1..32). */
    @Test fun verifiesASignatureProducedByTheServer() {
        val pub = Base64.getDecoder().decode("ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ=")
        val payload = """{"action":"update_app","display_id":7,"exp_ms":1763300000000,"params":{"sha256":"abababababababababababababababababababababababababababababababab","size":120158154,"version":"3.1.1"},"request_id":"0123456789abcdef","seq":412,"v":1}"""
        val sig = "gwyzRJvWD7JEOuYNLaDF5bShteMITfdIzMfsSH2VDv9SFKs8lRzFCTTu9P+Sw97lzhzbuoqQGH8JialcOvezDQ=="
        val c = JSONObject().put("id", ID).put("action", "update_app").put("params", JSONObject())
            .put("signed", JSONObject().put("payload", payload).put("sig", sig))
        assertEquals(Result.Accept(ID, 412, Action.Update("3.1.1", SHA, 120158154)), verify(c, env(key = pub)))
        c.put("signed", JSONObject().put("payload", payload.replace("120158154", "120158155")).put("sig", sig))
        assertEquals("bad_signature", reason(verify(c, env(key = pub))))
    }

    // ---- the checklist ----------------------------------------------------------------------

    @Test fun acceptsAGoodReboot() {
        assertEquals(Result.Accept(ID, 412, Action.Reboot), verify(cmd()))
    }

    @Test fun signatureFailures() {
        assertEquals("bad_signature", reason(verify(cmd(signer = keys(50)))))
        assertEquals("bad_signature", reason(verify(cmd(sig = "not base64!!"))))
        assertEquals("bad_signature", reason(verify(cmd(sig = Base64.getEncoder().encodeToString(ByteArray(10))))))
        val swapped = cmd()
        swapped.put("signed", JSONObject().put("payload", payload(seq = 999)).put("sig", swapped.getJSONObject("signed").getString("sig")))
        assertEquals("bad_signature", reason(verify(swapped)))
        val noSigned = cmd().also { it.remove("signed") }
        assertEquals("bad_signature", reason(verify(noSigned)))
        assertEquals("bad_signature", reason(verify(cmd(), env(key = null))))
        assertEquals("bad_signature", reason(verify(cmd(), env(changed = true))))
    }

    @Test fun payloadMustMatchTheCommandAndThisDisplay() {
        assertEquals("wrong_display", reason(verify(cmd(payload = payload(display = 8)))))
        assertEquals("wrong_display", reason(verify(cmd(), env(display = null))))
        assertEquals("wrong_display", reason(verify(cmd(), env(display = 8))))
        assertEquals("bad_signature", reason(verify(cmd(payload = payload(id = "fedcba9876543210")))))
        assertEquals("bad_signature", reason(verify(cmd(action = "set_startup"))))
        assertEquals("bad_signature", reason(verify(cmd(payload = payload(v = 2)))))
    }

    @Test fun expiryUsesTheServersClock() {
        assertEquals("expired", reason(verify(cmd(payload = payload(exp = NOW)))))
        assertEquals("expired", reason(verify(cmd(payload = payload(exp = NOW - 1)))))
        assertEquals("expired", reason(verify(cmd(), env(time = null))))
        assertTrue(verify(cmd(payload = payload(exp = NOW + 1))) is Result.Accept)
    }

    @Test fun sequenceMustGrow() {
        assertEquals("replayed", reason(verify(cmd(payload = payload(seq = 100)), env(last = 100))))
        assertEquals("replayed", reason(verify(cmd(payload = payload(seq = 99)), env(last = 100))))
        assertTrue(verify(cmd(payload = payload(seq = 101)), env(last = 100)) is Result.Accept)
    }

    @Test fun handledAndMalformedCommandsAreIgnored() {
        assertEquals(Result.Ignore, verify(cmd(), handled = setOf(ID)))
        assertEquals(Result.Ignore, verify(null))
        assertEquals(Result.Ignore, verify("text"))
        assertEquals(Result.Ignore, verify(cmd(id = "short", payload = payload(id = "short"))))
        assertEquals(Result.Ignore, verify(JSONObject("""{"action":"reboot_device"}""")))
    }

    @Test fun theUnsignedParamsAreNeverUsed() {
        val p = payload("update_app", """{"version":"3.1.1","sha256":"$SHA","size":1000}""")
        val c = cmd(payload = p, action = "update_app", unsignedParams = """{"version":"6.6.6","sha256":"${"cd".repeat(32)}","size":5}""")
        assertEquals(Result.Accept(ID, 412, Action.Update("3.1.1", SHA, 1000)), verify(c))
        assertEquals(Result.Accept(ID, 412, Action.Reboot), verify(cmd(unsignedParams = """{"anything":"at all"}""")))
    }

    // ---- capabilities, switches and typed params ------------------------------------------

    @Test fun switchesAndCapabilities() {
        assertEquals("not_allowed", reason(verify(cmd(), env(settings = AgentSettings(false, false, false)))))
        assertEquals("not_allowed", reason(verify(cmd(), env(settings = allOn.copy(reboot = false)))))
        assertEquals("unsupported", reason(verify(cmd(), env(c = caps.copy(canReboot = false)))))
        val up = payload("update_app", """{"version":"3.1.1","sha256":"$SHA","size":1000}""")
        assertEquals("not_allowed", reason(verify(cmd(up, "update_app"), env(settings = allOn.copy(update = false)))))
        assertEquals("unsupported", reason(verify(cmd(up, "update_app"), env(c = caps.copy(canUpdate = false)))))
        val st = payload("set_startup", """{"autostart":true}""")
        assertEquals("not_allowed", reason(verify(cmd(st, "set_startup"), env(settings = allOn.copy(startup = false)))))
        assertEquals("unsupported", reason(verify(cmd(payload("shutdown"), "shutdown"))))
    }

    @Test fun rebootTakesNoParams() {
        assertEquals("bad_params", reason(verify(cmd(payload = payload(params = """{"force":true}""")))))
        assertEquals("bad_params", reason(verify(cmd(payload = payload(params = "[]")))))
    }

    @Test fun startupParams() {
        fun run(params: String) = verify(cmd(payload(action = "set_startup", params = params), "set_startup"))
        assertEquals(Result.Accept(ID, 412, Action.Startup(true, "app")), run("""{"autostart":true,"mode":"app"}"""))
        assertEquals(Result.Accept(ID, 412, Action.Startup(false, null)), run("""{"autostart":false}"""))
        assertEquals(Result.Accept(ID, 412, Action.Startup(null, null)), run("{}"))
        assertEquals(Result.Accept(ID, 412, Action.Startup(null, "shell")), run("""{"mode":"shell"}"""))
        assertEquals("bad_params", reason(run("""{"autostart":"yes"}""")))
        assertEquals("bad_params", reason(run("""{"autostart":1}""")))
        assertEquals("bad_params", reason(run("""{"mode":"kiosk"}""")))
        assertEquals("bad_params", reason(run("""{"mode":3}""")))
        assertEquals("bad_params", reason(run("""{"autostart":true,"extra":1}""")))
    }

    @Test fun updateParams() {
        fun run(params: String) = verify(cmd(payload(action = "update_app", params = params), "update_app"))
        val ok = Action.Update("3.1.1", SHA, 1000)
        assertEquals(Result.Accept(ID, 412, ok), run("""{"version":"3.1.1","sha256":"$SHA","size":1000}"""))
        assertEquals(Result.Accept(ID, 412, ok), run("""{"version":"3.1.1","sha256":"${SHA.uppercase()}","size":1000}"""))
        for (bad in listOf(
            """{"version":"3.1.1","sha256":"$SHA"}""",
            """{"version":"3.1.1","sha256":"$SHA","size":1000,"url":"http://evil/x.apk"}""",
            """{"version":"../../x","sha256":"$SHA","size":1000}""",
            """{"version":"3.1.1 && calc","sha256":"$SHA","size":1000}""",
            """{"version":"","sha256":"$SHA","size":1000}""",
            """{"version":"3.1.1","sha256":"${"a".repeat(63)}","size":1000}""",
            """{"version":"3.1.1","sha256":"${"g".repeat(64)}","size":1000}""",
            """{"version":"3.1.1","sha256":"$SHA","size":0}""",
            """{"version":"3.1.1","sha256":"$SHA","size":-5}""",
            """{"version":"3.1.1","sha256":"$SHA","size":1000.5}""",
            """{"version":"3.1.1","sha256":"$SHA","size":"1000"}""",
            """{"version":"3.1.1","sha256":"$SHA","size":${SignedCommand.MAX_PACKAGE_BYTES + 1}}""",
            """{"version":3,"sha256":"$SHA","size":1000}""",
        )) assertEquals(bad, "bad_params", reason(run(bad)))
    }

    @Test fun tierBDetection() {
        assertTrue(SignedCommand.isTierB(JSONObject("""{"action":"reboot_device"}""")))
        assertTrue(SignedCommand.isTierB(JSONObject("""{"action":"update_app"}""")))
        assertFalse(SignedCommand.isTierB(JSONObject("""{"action":"restart_player"}""")))
        assertFalse(SignedCommand.isTierB(JSONObject("""{"action":5}""")))
        assertFalse(SignedCommand.isTierB("reboot_device"))
    }

    // ---- key pinning --------------------------------------------------------------------------

    @Test fun keyPinning() {
        val a = ByteArray(32) { 1 }
        val b = ByteArray(32) { 2 }
        assertArrayEquals(a, (KeyPin.decide(null, a) as KeyPin.Decision.Pin).key)
        assertEquals(KeyPin.Decision.Same, KeyPin.decide(a, a.copyOf()))
        assertEquals(KeyPin.Decision.Changed, KeyPin.decide(a, b))
        assertEquals(KeyPin.Decision.Unusable, KeyPin.decide(a, null))
        assertEquals(KeyPin.Decision.Unusable, KeyPin.decide(null, null))
    }

    @Test fun parsesTheKeyEndpoint() {
        val b64 = Base64.getEncoder().encodeToString(kp.pub)
        assertArrayEquals(kp.pub, KeyPin.parseKey("""{"public_key":"$b64","fingerprint":"x"}"""))
        assertNull(KeyPin.parseKey("""{"public_key":"${Base64.getEncoder().encodeToString(ByteArray(31))}"}"""))
        assertNull(KeyPin.parseKey("""{"public_key":"%%%"}"""))
        assertNull(KeyPin.parseKey("""{"public_key":5}"""))
        assertNull(KeyPin.parseKey("not json"))
    }

    @Test fun fingerprintMatchesTheServersFormat() {
        val hex = MessageDigest.getInstance("SHA-256").digest(ByteArray(32)).joinToString("") { "%02x".format(it) }.take(16)
        assertEquals(hex.chunked(4).joinToString(" "), KeyPin.fingerprint(ByteArray(32)))
        assertEquals(19, KeyPin.fingerprint(ByteArray(32)).length)
    }

    // ---- device-side rules ------------------------------------------------------------------------

    @Test fun rebootCooldown() {
        val now = 10_000_000_000L
        assertTrue(TierRules.rebootAllowed(0, now))
        assertFalse(TierRules.rebootAllowed(now - 60_000, now))
        assertFalse(TierRules.rebootAllowed(now - 599_999, now))
        assertTrue(TierRules.rebootAllowed(now - 600_000, now))
        assertTrue(TierRules.rebootAllowed(now + 3_600_000, now))
    }

    @Test fun otherTierRules() {
        assertTrue(TierRules.alreadyInstalled("3.0.0", "3.0.0"))
        assertFalse(TierRules.alreadyInstalled("3.0.0", "3.0.1"))
        assertFalse(TierRules.enoughSpace(100, 100))
        assertTrue(TierRules.enoughSpace(1_000_000_000, 100_000_000))
        assertTrue(TierRules.startAfterUpdate(true, false, false, updatePending = true))
        assertTrue(TierRules.startAfterUpdate(true, true, false, false))
        assertTrue(TierRules.startAfterUpdate(true, false, true, false))
        assertFalse(TierRules.startAfterUpdate(true, false, false, false))
        assertFalse(TierRules.startAfterUpdate(false, true, true, true))
        assertTrue(TierRules.startAtBoot(true, true))
        assertFalse(TierRules.startAtBoot(true, false))
        assertFalse(TierRules.startAtBoot(false, true))
    }

    @Test fun packageVerifier() {
        val data = ByteArray(1000) { it.toByte() }
        val sha = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
        fun feed(v: PackageVerifier, d: ByteArray, chunk: Int): Boolean {
            var i = 0
            while (i < d.size) {
                val n = minOf(chunk, d.size - i)
                if (!v.update(d.copyOfRange(i, i + n), n)) return false
                i += n
            }
            return true
        }
        PackageVerifier(1000, sha).also { assertTrue(feed(it, data, 300)); assertEquals(PackageVerifier.Verdict.OK, it.finish()) }
        PackageVerifier(1000, sha.uppercase()).also { feed(it, data, 1000); assertEquals(PackageVerifier.Verdict.OK, it.finish()) }
        PackageVerifier(1000, sha).also { feed(it, data.copyOf(999), 100); assertEquals(PackageVerifier.Verdict.BAD_SIZE, it.finish()) }
        PackageVerifier(999, sha).also { assertFalse(feed(it, data, 100)) }
        PackageVerifier(1000, "00".repeat(32)).also { feed(it, data, 1000); assertEquals(PackageVerifier.Verdict.BAD_HASH, it.finish()) }
        val tampered = data.copyOf().also { it[5] = 99 }
        PackageVerifier(1000, sha).also { feed(it, tampered, 1000); assertEquals(PackageVerifier.Verdict.BAD_HASH, it.finish()) }
    }

    // ---- settings and the poll body ------------------------------------------------------------------

    @Test fun settingsCarryTheTierBSwitches() {
        val s = AgentSettings.parse("""{"settings":{"agent":{"reboot":true,"startup":"yes","update":true}}}""")!!
        assertEquals(AgentSettings(false, false, false, reboot = true, startup = false, update = true), s)
        assertTrue(s.polls)
        assertFalse(AgentSettings(true, false, false).polls)
        assertTrue(AgentSettings(false, false, false, update = true).polls)
    }

    @Test fun serverTime() {
        assertEquals(1763299000000L, AgentCommands.serverTimeMs("""{"commands":[],"server_time_ms":1763299000000}"""))
        assertNull(AgentCommands.serverTimeMs("""{"commands":[],"server_time_ms":"x"}"""))
        assertNull(AgentCommands.serverTimeMs("""{"commands":[]}"""))
        assertNull(AgentCommands.serverTimeMs("nope"))
    }

    // ---- server time high-water, time-based seq, update error ---------------------------------

    @Test fun serverTimeMustNotGoBackwards() {
        assertTrue(TierRules.serverTimeOk(0, NOW))
        assertTrue(TierRules.serverTimeOk(NOW, NOW))
        assertTrue(TierRules.serverTimeOk(NOW, NOW + 1))
        assertFalse("an old response replayed to make an old command look valid", TierRules.serverTimeOk(NOW, NOW - 1))
    }

    @Test fun rePairingForgetsTheSequenceAndTheTimeMarkToo() {
        for (k in listOf("pin_ab12", "kc_ab12", "seq_ab12", "st_ab12")) assertTrue(k, TierRules.forgottenOnRepair(k))
        for (k in listOf("display_id", "last_reboot", "handled", "reboot", "update_error")) assertFalse(k, TierRules.forgottenOnRepair(k))
    }

    @Test fun aTimeBasedSeqIsAccepted() {
        val seq = 1_763_299_000_123L
        val r = verify(cmd(payload(seq = seq)), env(last = seq - 1))
        assertEquals(seq, (r as Result.Accept).seq)
        assertEquals(SignedCommand.REPLAYED, reason(verify(cmd(payload(seq = seq)), env(last = seq))))
    }

    @Test fun updateErrorIsShownUntilTheVersionIsInstalledOrADayPasses() {
        val stored = TierRules.storeUpdateError("3.1.1", "conflict (signature or downgrade)", NOW)
        assertEquals("3.1.1: conflict (signature or downgrade)", TierRules.showUpdateError(stored, "3.1.0", NOW + 1_000))
        assertNull(TierRules.showUpdateError(stored, "3.1.1", NOW + 1_000))
        assertNull(TierRules.showUpdateError(stored, "3.1.0", NOW + 25 * 3_600_000L))
        assertNull(TierRules.showUpdateError(null, "3.1.0", NOW))
        assertNull(TierRules.showUpdateError("garbage", "3.1.0", NOW))
        assertFalse(TierRules.updateErrorText("1", "a|b").contains('|'))
    }
}
