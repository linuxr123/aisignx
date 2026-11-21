package com.aisignx.player.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class SignerTest {
    private val a = "27a18460d609a17edfc724ce4f9600e3209bf28b05d839b86f97d2d8e29d25e9"
    private val b = "889bc7179e4efc68222e2ebcc406e2150088ce03c0a36916f664d0825b14574d"

    @Test fun `the digest is the sha-256 of the certificate`() {
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", Signer.hex(ByteArray(0)))
        assertEquals(64, Signer.hex(byteArrayOf(1, 2, 3)).length)
    }

    @Test fun `a different key is reported and the same key (any case) is not`() {
        assertNotNull(Signer.mismatch(a, b))
        assertNull(Signer.mismatch(a, a.uppercase()))
    }

    @Test fun `an unreadable key never blocks an update`() {
        assertNull(Signer.mismatch(null, b))
        assertNull(Signer.mismatch(a, null))
        assertNull(Signer.mismatch(null, null))
    }
}
