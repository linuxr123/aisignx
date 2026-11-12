package com.aisignx.player.net

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.security.cert.CertificateException

class TlsPinTest {

    private lateinit var server: MockWebServer
    // Self-signed, and its name does not match 127.0.0.1 (LAN certificates often lack the IP).
    private val held = HeldCertificate.Builder()
        .commonName("lan")
        .addSubjectAlternativeName("signage.invalid")
        .build()
    private val pin get() = Tls.fingerprint(held.certificate.encoded)
    private lateinit var origin: String

    @Before fun setUp() {
        server = MockWebServer()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(held).build().sslSocketFactory(), false)
        repeat(3) {
            server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
                .setBody("""{"status":"ok","app":"aisignx","api":1}"""))
        }
        server.start()
        origin = "https://127.0.0.1:${server.port}"
    }

    @After fun tearDown() = server.shutdown()

    @Test fun pinMatchIsAccepted() {
        assertEquals(Http.Health.Ok, Http.health(Http.build(pin), origin))
    }

    @Test fun pinMismatchIsRejected() {
        val wrong = "0".repeat(64)
        assertTrue(Http.health(Http.build(wrong), origin) is Http.Health.Untrusted)
        assertEquals(0, server.requestCount)
    }

    @Test fun noPinSelfSignedIsRejected() {
        assertTrue(Http.health(Http.build(null), origin) is Http.Health.Untrusted)
        assertEquals(0, server.requestCount)
    }

    @Test fun captureReturnsFingerprintWithoutSendingHttp() {
        val info = Tls.capture(origin)
        assertNotNull(info)
        assertEquals(pin, info!!.sha256)
        assertEquals("127.0.0.1", info.host)
        assertTrue(info.selfSigned)
        assertEquals(0, server.requestCount)
    }

    @Test fun captureOfPlainHttpIsNull() {
        assertEquals(null, Tls.capture("http://127.0.0.1:${server.port}"))
    }

    @Test(expected = CertificateException::class)
    fun trustManagerWithoutPinThrows() {
        Tls.PinningTrustManager(null).checkServerTrusted(arrayOf(held.certificate), "RSA")
    }

    @Test fun trustManagerAcceptsOnlyPinnedLeaf() {
        Tls.PinningTrustManager(pin).checkServerTrusted(arrayOf(held.certificate), "RSA")
        val other = HeldCertificate.Builder().commonName("other").build()
        try {
            Tls.PinningTrustManager(pin).checkServerTrusted(arrayOf(other.certificate), "RSA")
            throw AssertionError("other certificate accepted")
        } catch (_: CertificateException) {
        }
    }

    @Test fun colonHex() {
        assertEquals("AB:CD:01", Tls.colonHex("abcd01"))
    }
}
