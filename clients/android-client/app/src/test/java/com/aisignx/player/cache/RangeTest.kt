package com.aisignx.player.cache

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class RangeTest {

    private fun partial(start: Long, end: Long) = Range.Result.Partial(start, end)

    @Test fun openEnded() = assertEquals(partial(0, 999), Range.parse("bytes=0-", 1000))

    @Test fun closed() = assertEquals(partial(100, 199), Range.parse("bytes=100-199", 1000))

    @Test fun endClampedToFile() = assertEquals(partial(900, 999), Range.parse("bytes=900-5000", 1000))

    @Test fun suffix() {
        assertEquals(partial(900, 999), Range.parse("bytes=-100", 1000))
        assertEquals(partial(0, 999), Range.parse("bytes=-5000", 1000))
    }

    @Test fun pastTheEndIs416() {
        assertSame(Range.Result.Unsatisfiable, Range.parse("bytes=1000-", 1000))
        assertSame(Range.Result.Unsatisfiable, Range.parse("bytes=-0", 1000))
        assertSame(Range.Result.Unsatisfiable, Range.parse("bytes=0-", 0))
    }

    @Test fun invalidOrMultiIsFull() {
        listOf(null, "", "bytes=", "bytes=-", "items=0-1", "bytes=a-b", "bytes=5-1", "bytes=0-1,5-9",
            "bytes=99999999999999999999-")
            .forEach { assertSame(it.toString(), Range.Result.Full, Range.parse(it, 1000)) }
    }

    @Test fun headers() {
        val p = partial(10, 19)
        assertEquals(10L, p.length)
        assertEquals("bytes 10-19/100", Range.contentRange(p, 100))
        assertEquals("bytes */100", Range.unsatisfiedRange(100))
    }
}
