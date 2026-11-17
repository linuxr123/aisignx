package com.aisignx.player.shell

import net.i2p.crypto.eddsa.EdDSAEngine
import net.i2p.crypto.eddsa.EdDSAPublicKey
import net.i2p.crypto.eddsa.spec.EdDSANamedCurveTable
import net.i2p.crypto.eddsa.spec.EdDSAPublicKeySpec
import java.security.MessageDigest

/**
 * Ed25519 signature check, the only crypto the signed device commands need (docs/signed-commands.md).
 * Uses the pure-Java net.i2p.crypto:eddsa library because the JDK has Ed25519 only from Android 13
 * (API 33) and the app supports API 26. Any problem at all (bad key length, bad point, bad
 * signature length, a library error) is "not verified": it never throws.
 */
object Ed25519 {
    const val KEY_BYTES = 32
    const val SIG_BYTES = 64

    fun verify(publicKey: ByteArray, message: ByteArray, signature: ByteArray): Boolean {
        if (publicKey.size != KEY_BYTES || signature.size != SIG_BYTES) return false
        return try {
            val spec = EdDSANamedCurveTable.getByName(EdDSANamedCurveTable.ED_25519)
            val key = EdDSAPublicKey(EdDSAPublicKeySpec(publicKey, spec))
            val engine = EdDSAEngine(MessageDigest.getInstance(spec.hashAlgorithm))
            engine.initVerify(key)
            engine.update(message)
            engine.verify(signature)
        } catch (_: Exception) {
            false
        }
    }
}
