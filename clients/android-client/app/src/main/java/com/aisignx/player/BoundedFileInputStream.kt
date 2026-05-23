package com.aisignx.player

import java.io.InputStream
import java.io.RandomAccessFile

/**
 * InputStream that reads up to `length` bytes from a RandomAccessFile and
 * then reports EOF. Used by WebCache.serveMedia() to stream a Range slice
 * of a cached video/image without ever loading the whole file into memory.
 *
 * The RandomAccessFile must already be seek()ed to the desired start
 * offset before being wrapped.
 */
class BoundedFileInputStream(
	private val raf: RandomAccessFile,
	length: Long
) : InputStream() {
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
		val want = minOf(len.toLong(), remaining).toInt()
		val n = raf.read(b, off, want)
		if (n < 0) { remaining = 0L; return -1 }
		remaining -= n
		return n
	}

	override fun available(): Int =
		if (remaining > Int.MAX_VALUE) Int.MAX_VALUE else remaining.toInt()

	override fun close() {
		try { raf.close() } catch (_: Throwable) {}
	}
}
