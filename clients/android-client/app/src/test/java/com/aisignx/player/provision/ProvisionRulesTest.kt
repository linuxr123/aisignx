package com.aisignx.player.provision

import com.aisignx.player.provision.ProvisionRules.Claim
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProvisionRulesTest {

    @Test fun provisioningModeIsFullyManagedOrNothing() {
        assertEquals(1, ProvisionRules.chooseMode(listOf(1, 2)))
        assertEquals(1, ProvisionRules.chooseMode(listOf(1)))
        assertEquals(1, ProvisionRules.chooseMode(null))
        assertEquals(1, ProvisionRules.chooseMode(emptyList()))
        assertNull(ProvisionRules.chooseMode(listOf(2)))
        assertNull(ProvisionRules.chooseMode(listOf(2, 3)))
    }

    @Test fun parsesTheExtrasBundle() {
        val e = ProvisionRules.parseExtras("http://192.168.1.20:5000", "AbC-123_xyz")!!
        assertEquals("http://192.168.1.20:5000", e.serverUrl)
        assertEquals("AbC-123_xyz", e.secret)
        assertEquals("https://signage.example.lan", ProvisionRules.parseExtras("https://signage.example.lan/", "s3cret")!!.serverUrl)
    }

    @Test fun rejectsBadExtras() {
        assertNull(ProvisionRules.parseExtras(null, "s3cret"))
        assertNull(ProvisionRules.parseExtras("", "s3cret"))
        assertNull(ProvisionRules.parseExtras("ftp://host", "s3cret"))
        assertNull(ProvisionRules.parseExtras("http://host/path", "s3cret"))
        assertNull(ProvisionRules.parseExtras("http://host", null))
        assertNull(ProvisionRules.parseExtras("http://host", ""))
        assertNull(ProvisionRules.parseExtras("http://host", "has space"))
        assertNull(ProvisionRules.parseExtras("http://host", "line\nbreak"))
        assertNull(ProvisionRules.parseExtras("http://host", "x".repeat(129)))
        assertNotNull(ProvisionRules.parseExtras("http://host", "x".repeat(128)))
    }

    @Test fun theSecretIsNeverPrinted() {
        val e = ProvisionRules.parseExtras("http://host:5000", "TopSecretValue")!!
        assertFalse(e.toString().contains("TopSecretValue"))
    }

    @Test fun classifiesTheClaimAnswer() {
        assertEquals(Claim.PAIRED, ProvisionRules.classify(200, "paired"))
        assertEquals(Claim.RETRY, ProvisionRules.classify(200, "pending"))
        assertEquals(Claim.RETRY, ProvisionRules.classify(200, null))
        assertEquals(Claim.RETRY, ProvisionRules.classify(429, null))
        assertEquals(Claim.RETRY, ProvisionRules.classify(500, null))
        assertEquals(Claim.RETRY, ProvisionRules.classify(503, null))
        assertEquals(Claim.GIVE_UP, ProvisionRules.classify(404, null))
        assertEquals(Claim.GIVE_UP, ProvisionRules.classify(400, null))
        assertEquals(Claim.GIVE_UP, ProvisionRules.classify(401, null))
        assertEquals(Claim.GIVE_UP, ProvisionRules.classify(302, null))
    }

    @Test fun readsTheStatusMember() {
        assertEquals("paired", ProvisionRules.statusOf("""{"status":"paired","display":{"id":1}}"""))
        assertNull(ProvisionRules.statusOf("""{"status":5}"""))
        assertNull(ProvisionRules.statusOf("nope"))
    }

    @Test fun retryDelayGrowsThenStays() {
        assertEquals(listOf(3_000L, 5_000L, 10_000L, 20_000L, 30_000L, 30_000L), (1..6).map { ProvisionRules.delayMs(it) })
        assertEquals(3_000L, ProvisionRules.delayMs(0))
    }

    @Test fun onlyTheDisplayCookieIsKept() {
        val kept = ProvisionRules.displayCookies(
            listOf("aisignx_display=abc; Max-Age=100; Path=/; HttpOnly; SameSite=Strict", "session=zzz; Path=/", "evil=1"),
        )
        assertEquals(1, kept.size)
        assertTrue(kept[0].startsWith("aisignx_display="))
        assertTrue(ProvisionRules.displayCookies(emptyList()).isEmpty())
    }

    private val KEY_B64 = java.util.Base64.getEncoder().encodeToString(ByteArray(32) { it.toByte() })

    @Test fun parsesAndValidatesTheCommandKey() {
        val e = ProvisionRules.parseExtras("http://host:5000", "s3cret", KEY_B64)!!
        assertEquals(32, e.commandKey!!.size)
        assertNull(ProvisionRules.parseExtras("http://host", "s3cret")!!.commandKey)
        assertNull("31 bytes", ProvisionRules.parseCommandKey(java.util.Base64.getEncoder().encodeToString(ByteArray(31))))
        assertNull("33 bytes", ProvisionRules.parseCommandKey(java.util.Base64.getEncoder().encodeToString(ByteArray(33))))
        assertNull("not base64", ProvisionRules.parseCommandKey("not base64!!"))
        assertNull(ProvisionRules.parseCommandKey(""))
        assertNull(ProvisionRules.parseCommandKey("A".repeat(65)))
        // a bad key never rejects the set-up; it only falls back to pinning on first contact
        assertNull(ProvisionRules.parseExtras("http://host", "s3cret", "junk")!!.commandKey)
    }

    @Test fun pathsAreRejectedExactlyLikeTheServerDoes() {
        // server/aisignx/provision clean_base_url rejects a path; both sides must agree (no path).
        for (u in listOf("http://host/signage", "https://host/a/b", "http://host/?x=1", "http://user@host", "http://host#f")) {
            assertNull(u, ProvisionRules.parseExtras(u, "s3cret"))
        }
        assertNotNull(ProvisionRules.parseExtras("http://host/", "s3cret"))
    }

    @Test fun onlyRealCertificateProblemsGiveUp() {
        fun ssl(cause: Throwable?) = javax.net.ssl.SSLHandshakeException("x").also { if (cause != null) it.initCause(cause) }
        assertEquals(Claim.GIVE_UP, ProvisionRules.classifyFailure(javax.net.ssl.SSLPeerUnverifiedException("host")))
        assertEquals(Claim.GIVE_UP, ProvisionRules.classifyFailure(ssl(java.security.cert.CertificateException("untrusted"))))
        assertEquals(Claim.GIVE_UP, ProvisionRules.classifyFailure(ssl(java.security.cert.CertPathValidatorException("path"))))
        assertEquals("interrupted handshake", Claim.RETRY, ProvisionRules.classifyFailure(ssl(null)))
        assertEquals("closed by peer", Claim.RETRY, ProvisionRules.classifyFailure(javax.net.ssl.SSLException("Connection closed by peer")))
        assertEquals(
            "a reset tablet's clock is behind",
            Claim.RETRY,
            ProvisionRules.classifyFailure(ssl(java.security.cert.CertPathValidatorException("v", java.security.cert.CertificateNotYetValidException("not yet")))),
        )
        assertEquals(Claim.RETRY, ProvisionRules.classifyFailure(ssl(java.security.cert.CertificateNotYetValidException())))
    }

    @Test fun setUpIsBoundedInTime() {
        assertFalse(ProvisionRules.timedOut(0, 9 * 60_000L))
        assertTrue(ProvisionRules.timedOut(0, 10 * 60_000L))
    }

    @Test fun sevenQuickTapsLeave() {
        val c = ProvisionRules.TapCounter()
        for (i in 0 until 6) assertFalse(c.tap(i * 500L))
        assertTrue(c.tap(3_000L))
        assertFalse("counter starts over", c.tap(3_100L))
    }

    @Test fun slowTapsNeverLeave() {
        val c = ProvisionRules.TapCounter()
        for (i in 0 until 30) assertFalse(c.tap(i * 3_000L))
    }

    // The server sets the display cookie for the path /api/player (server player/auth.py). Reading it back at the
    // site root never returns it, which made the first real tablet retry the pairing forever.
    @Test fun theDisplayCookieIsSetAndReadAtItsOwnPath() {
        assertEquals("http://192.168.1.19:5000/api/player/", ProvisionRules.cookieUrl("http://192.168.1.19:5000"))
        assertEquals("https://signs.example.com/api/player/", ProvisionRules.cookieUrl("https://signs.example.com/"))
    }
}
