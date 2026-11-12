package com.aisignx.player.cache

import java.io.InputStream
import java.io.RandomAccessFile

/**
 * A response the shell serves from its caches. Kept free of android.* so the caches
 * can be unit tested on the JVM; PlayerWebClient turns it into a WebResourceResponse.
 */
class Served(
    val status: Int,
    val reason: String,
    val mime: String,
    val charset: String?,
    val headers: Map<String, String>,
    val body: InputStream,
)

/**
 * Reads at most [length] bytes from a RandomAccessFile that is already positioned at
 * the start of the slice, then reports EOF. Streams a Range slice of a cached file
 * without loading it into memory. Ported from v2.
 */
class BoundedFileInputStream(private val raf: RandomAccessFile, length: Long) : InputStream() {
    private var remaining: Long = length

    override fun read(): Int {
        if (remaining <= 0L) return -1
        val b = raf.read()
        if (b < 0) { remaining = 0L; return -1 }
        remaining--
        return b
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (remaining <= 0L) return -1
        if (len == 0) return 0
        val want = minOf(len.toLong(), remaining).toInt()
        val n = raf.read(b, off, want)
        if (n < 0) { remaining = 0L; return -1 }
        remaining -= n
        return n
    }

    override fun available(): Int = if (remaining > Int.MAX_VALUE) Int.MAX_VALUE else remaining.toInt()

    override fun close() {
        try { raf.close() } catch (_: Throwable) { }
    }
}
