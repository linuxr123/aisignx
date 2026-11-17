package com.aisignx.player.shell

import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException

/**
 * Downloads the installer for an open update_app command into app-private storage, checking the
 * signed size and SHA-256 while it streams. The URL is built by the caller from the server origin
 * and the (validated) request id; the display token goes only in the Authorization header and is
 * never followed to another host (the client does not follow redirects).
 */
object PackageDownload {
    sealed class Outcome {
        object Ok : Outcome()
        /** [why] is a short fixed text safe to send in an ack. */
        data class Failed(val why: String) : Outcome()
    }

    private const val CHUNK = 64 * 1024

    fun fetch(client: OkHttpClient, url: String, token: String, dest: File, size: Long, sha256: String): Outcome {
        val part = File(dest.parentFile, dest.name + ".part")
        try {
            dest.parentFile?.mkdirs()
            part.delete()
            dest.delete()
            val req = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
            client.newCall(req).execute().use { r ->
                if (r.code != 200) return Outcome.Failed("download failed (HTTP ${r.code})")
                val body = r.body ?: return Outcome.Failed("download failed")
                val v = PackageVerifier(size, sha256)
                val buf = ByteArray(CHUNK)
                part.outputStream().use { out ->
                    body.byteStream().use { input ->
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            if (!v.update(buf, n)) return Outcome.Failed("package larger than signed size")
                            out.write(buf, 0, n)
                        }
                    }
                }
                when (v.finish()) {
                    PackageVerifier.Verdict.OK -> Unit
                    PackageVerifier.Verdict.BAD_SIZE -> return Outcome.Failed("package size mismatch")
                    PackageVerifier.Verdict.BAD_HASH -> return Outcome.Failed("package checksum mismatch")
                }
            }
            if (!part.renameTo(dest)) return Outcome.Failed("could not store the package")
            return Outcome.Ok
        } catch (e: IOException) {
            return Outcome.Failed("download failed")
        } finally {
            part.delete()
        }
    }
}
