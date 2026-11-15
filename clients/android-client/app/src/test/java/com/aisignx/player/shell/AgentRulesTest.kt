package com.aisignx.player.shell

import com.aisignx.player.shell.AgentCommands.Verdict
import com.aisignx.player.shell.Watchdog.Step
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.util.zip.GZIPInputStream

class AgentRulesTest {

    private val ID = "0123456789abcdef"
    private fun cmd(action: Any?, id: Any? = ID, extra: String = "") =
        JSONObject("""{"id":${if (id is String) "\"$id\"" else id},"action":${if (action is String) "\"$action\"" else action},"params":{}$extra}""")

    // ---- settings ---------------------------------------------------------------

    @Test fun settingsParse() {
        assertNull(AgentSettings.parse("not json"))
        assertNull(AgentSettings.parse("""{"items":[]}"""))
        assertEquals(AgentSettings.OFF, AgentSettings.parse("""{"settings":{"volume":3}}"""))
        val s = AgentSettings.parse("""{"settings":{"agent":{"watchdog":true,"restart":"yes","logs":true}}}""")!!
        assertEquals(AgentSettings(watchdog = true, restart = false, logs = true), s)
        assertTrue(s.polls)
        assertFalse(AgentSettings(true, false, false).polls)
    }

    // ---- command validation -----------------------------------------------------

    private val on = AgentSettings(true, true, true)

    @Test fun validCommandsRun() {
        assertEquals(Verdict.Run(ID, "restart_player"), AgentCommands.check(cmd("restart_player"), on, emptySet()))
        assertEquals(Verdict.Run(ID, "collect_logs"), AgentCommands.check(cmd("collect_logs"), on, emptySet()))
    }

    @Test fun switchedOffAndUnknownAreRefused() {
        val logsOnly = AgentSettings(false, false, true)
        assertEquals(Verdict.Refuse(ID, "switched off"), AgentCommands.check(cmd("restart_player"), logsOnly, emptySet()))
        assertEquals(Verdict.Refuse(ID, "unsupported"), AgentCommands.check(cmd("reboot"), on, emptySet()))
        assertEquals(Verdict.Refuse(ID, "unsupported"), AgentCommands.check(cmd("rm -rf /"), on, emptySet()))
        assertEquals(Verdict.Refuse(ID, "unsupported"), AgentCommands.check(cmd(null), on, emptySet()))
        assertEquals(Verdict.Refuse(ID, "expired"), AgentCommands.check(cmd("collect_logs", extra = ""","expires_in_s":0"""), on, emptySet()))
    }

    @Test fun badIdsAndShapesAreIgnored() {
        for (bad in listOf("", "0123456789ABCDEF", "0123456789abcde", "0123456789abcdef0", "../../etc/passwd", "0123456789abcdeg")) {
            assertEquals("id '$bad'", Verdict.Ignore, AgentCommands.check(cmd("restart_player", bad), on, emptySet()))
        }
        assertEquals(Verdict.Ignore, AgentCommands.check(cmd("restart_player", 12345), on, emptySet()))
        assertEquals(Verdict.Ignore, AgentCommands.check("restart_player", on, emptySet()))
        assertEquals(Verdict.Ignore, AgentCommands.check(null, on, emptySet()))
        assertEquals(Verdict.Ignore, AgentCommands.check(JSONObject("""{"action":"restart_player"}"""), on, emptySet()))
    }

    @Test fun handledIdsRunAtMostOnce() {
        assertEquals(Verdict.Ignore, AgentCommands.check(cmd("restart_player"), on, setOf(ID)))
        var ids = emptyList<String>()
        for (i in 0 until 100) ids = AgentRates.rememberId(ids, "%016x".format(i))
        assertEquals(64, ids.size)
        assertEquals("%016x".format(99), ids.last())
    }

    @Test fun listIsBoundedAndTolerant() {
        assertEquals(0, AgentCommands.parseList("junk").size)
        assertEquals(0, AgentCommands.parseList("""{"commands":"x"}""").size)
        val many = (1..9).joinToString(",") { """{"id":"%016x","action":"collect_logs"}""".format(it) }
        assertEquals(5, AgentCommands.parseList("""{"commands":[$many]}""").size)
    }

    // ---- escalation ladder ------------------------------------------------------

    private fun at(silentS: Long, done: Int = 0, heavy: Int = 0, sinceLoadS: Long = 1000) =
        Watchdog.decide(nowMs = 10_000_000L, lastAliveMs = 10_000_000L - silentS * 1000, pageLoadedMs = 10_000_000L - sinceLoadS * 1000, doneLevel = done, heavyInWindow = heavy)

    @Test fun ladderSteps() {
        assertEquals(Step.NONE, at(44).step)
        assertEquals(Step.RELOAD_PAGE, at(45).step)
        assertEquals(Step.RECREATE_WEBVIEW, at(90, done = 1).step)
        assertEquals(Step.RECREATE_ACTIVITY, at(180, done = 2).step)
        assertEquals(Step.RESTART_PROCESS, at(300, done = 3).step)
        assertEquals(Step.RESTART_PROCESS, at(1000, done = 3).step)
    }

    @Test fun eachLevelActsOnceAndAliveResetsIt() {
        assertEquals(Step.NONE, at(60, done = 1).step)      // still level 1, already done
        assertEquals(Step.RELOAD_PAGE, at(50, done = 0).step) // after an alive the caller resets done to 0
    }

    @Test fun aLongSilenceJumpsToTheReachedLevel() {
        val d = at(200, done = 0)
        assertEquals(Step.RECREATE_ACTIVITY, d.step)
        assertEquals(3, d.level)
    }

    @Test fun graceAfterPageLoad() {
        assertEquals(Step.NONE, at(400, sinceLoadS = 59).step)
        assertEquals(Step.RESTART_PROCESS, at(400, done = 3, sinceLoadS = 60).step)
    }

    @Test fun heavyStepsAreCappedPerHour() {
        val d = at(180, done = 2, heavy = 1)
        assertEquals(Step.NONE, d.step)
        assertTrue(d.gaveUp)
        assertEquals(3, d.level)
        assertFalse(at(90, done = 1, heavy = 1).gaveUp)                  // light steps are not capped
        assertEquals(Step.RECREATE_WEBVIEW, at(90, done = 1, heavy = 1).step)
        assertTrue(at(300, done = 3, heavy = 1).gaveUp)
    }

    // ---- rates ------------------------------------------------------------------

    @Test fun recentCountsTheLastHourAndFutureTimes() {
        val now = 10_000_000_000L
        val times = listOf(now - AgentRates.HOUR_MS - 1, now - AgentRates.HOUR_MS + 1, now - 5, now + 99_999)
        assertEquals(3, AgentRates.recent(times, now))
    }

    @Test fun timesRoundTripAndAreBounded() {
        var t = emptyList<Long>()
        for (i in 1L..50L) t = AgentRates.add(t, i)
        assertEquals(20, t.size)
        assertEquals(t, AgentRates.parseTimes(AgentRates.formatTimes(t)))
        assertEquals(emptyList<Long>(), AgentRates.parseTimes("x,,y"))
    }

    @Test fun crashRestartOncePerTenMinutes() {
        val now = 100_000_000L
        assertTrue(AgentRates.crashRestartAllowed(0, now))
        assertFalse(AgentRates.crashRestartAllowed(now - 9 * 60_000, now))
        assertTrue(AgentRates.crashRestartAllowed(now - 10 * 60_000, now))
        assertFalse(AgentRates.crashRestartAllowed(now + 60_000, now))       // clock went back a little
        assertTrue(AgentRates.crashRestartAllowed(now + 3_600_000, now))     // implausibly far in the future
    }

    // ---- redaction and size -----------------------------------------------------

    @Test fun secretsAreRedacted() {
        val token = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde" // 43 chars
        val pin = "a".repeat(32) + "b".repeat(32)
        val raw = """
            [t] I/Http Authorization: Bearer $token
            [t] D/Web Cookie: aisignx_display=$token; other=1
            [t] D/Web Set-Cookie: aisignx_display=$token; Path=/api/player; HttpOnly
            [t] I/Cfg certPin=$pin
            [t] I/Cfg {"unlock":{"alg":"pbkdf2","salt":"c2FsdHNhbHQ=","hash":"deadbeefcafebabe"}}
            [t] I/Cfg unlock_hash=zzzz1234
            [t] W/X token in a url: https://s/x?token=sekret99&a=1
            [t] I/Player player state PLAYING
            [t] I/Player back key: leaving the player
        """.trimIndent()
        val out = LogPrep.redact(raw, listOf(token, pin))
        for (s in listOf(token, pin, "c2FsdHNhbHQ=", "deadbeefcafebabe", "zzzz1234", "sekret99", "other=1")) {
            assertFalse("leaked $s", out.contains(s))
        }
        assertTrue(out.contains("player state PLAYING"))
        assertTrue(out.contains("back key: leaving the player"))
    }

    @Test fun tokenOnlyWithoutLabelsIsRedactedByShape() {
        val t = "Zx".repeat(21) + "Q"
        assertFalse(LogPrep.redact("got $t here").contains(t))
        assertTrue(LogPrep.redact("path /api/player/v1/manifest ok").contains("/api/player/v1/manifest"))
    }

    @Test fun gzipKeepsTheNewestPartUnderTheLimit() {
        val rnd = java.util.Random(1)
        val text = (0 until 40_000).joinToString("\n") { "line $it " + java.lang.Long.toHexString(rnd.nextLong()) }
        val gz = LogPrep.gzipNewest(text, 100_000)
        assertTrue(gz.size <= 100_000)
        val back = GZIPInputStream(ByteArrayInputStream(gz)).readBytes().toString(Charsets.UTF_8)
        assertTrue(back.length < text.length)
        assertTrue(back.endsWith("line 39999 " + text.substringAfterLast("line 39999 ")))
        assertTrue(back.startsWith("line "))       // cut at a line start
        assertTrue(LogPrep.gzipNewest("small").size < 100)
    }

    @Test fun tokenFromCookieHeader() {
        val t = "A".repeat(43)
        assertEquals(t, LogPrep.tokenFromCookies("x=1; aisignx_display=$t; y=2"))
        assertNull(LogPrep.tokenFromCookies(null))
        assertNull(LogPrep.tokenFromCookies("aisignx_display=short"))
        assertNull(LogPrep.tokenFromCookies("aisignx_display=${t}\r\nX: y"))
        assertNull(LogPrep.tokenFromCookies("other=$t"))
    }
}
