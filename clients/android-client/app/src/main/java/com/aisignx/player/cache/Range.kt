package com.aisignx.player.cache

/**
 * Parses a single HTTP byte range ("bytes=a-b", "bytes=a-" or "bytes=-n") against a
 * file of [total] bytes. Multi-range and malformed headers are ignored (full 200
 * response), as RFC 9110 allows; a syntactically valid but unsatisfiable range is 416.
 */
object Range {

    sealed class Result {
        object Full : Result()
        data class Partial(val start: Long, val end: Long) : Result() {
            val length: Long get() = end - start + 1
        }
        object Unsatisfiable : Result()
    }

    private val SINGLE = Regex("""^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$""", RegexOption.IGNORE_CASE)

    fun parse(header: String?, total: Long): Result {
        if (header.isNullOrBlank()) return Result.Full
        val m = SINGLE.matchEntire(header) ?: return Result.Full
        val a = m.groupValues[1]
        val b = m.groupValues[2]
        if (a.isEmpty() && b.isEmpty()) return Result.Full
        if (a.length > 18 || b.length > 18) return Result.Full
        if (a.isEmpty()) {
            // Suffix range: the last n bytes.
            val n = b.toLong()
            if (n == 0L || total == 0L) return Result.Unsatisfiable
            val start = if (n >= total) 0L else total - n
            return Result.Partial(start, total - 1)
        }
        val start = a.toLong()
        if (start >= total) return Result.Unsatisfiable
        val end = if (b.isEmpty()) total - 1 else b.toLong()
        if (end < start) return Result.Full
        return Result.Partial(start, minOf(end, total - 1))
    }

    fun contentRange(p: Result.Partial, total: Long): String = "bytes ${p.start}-${p.end}/$total"

    fun unsatisfiedRange(total: Long): String = "bytes */$total"
}
