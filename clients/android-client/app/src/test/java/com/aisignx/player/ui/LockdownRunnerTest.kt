package com.aisignx.player.ui

import com.aisignx.player.ui.Lockdown.Mode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LockdownRunnerTest {

    private class Mem : LockdownRunner.State {
        override var engaged = false
        override var releasePending = false
        override var tripped = false
    }

    /** A pretend device. [failing] steps throw until removed from the set. */
    private class Dev(val failing: MutableSet<String> = mutableSetOf()) : Lockdown.Ops {
        override val packageName = "com.aisignx.player"
        override var stayOnBackup: Int? = null
        var keyguardOff = false
        var statusBarOff = false
        var homePinned = false
        var aliasOn = false
        val restrictions = mutableSetOf<String>()
        var calls = 0
        private fun call(n: String) { calls++; if (n in failing) throw IllegalStateException(n) }
        override fun currentStayOn() = 0
        override fun setLockTaskPackages(packages: List<String>) = call("lock_task_packages")
        override fun setLockTaskFeatures(features: Int) = call("lock_task_features")
        override fun setKeyguardDisabled(disabled: Boolean) { call("keyguard"); keyguardOff = disabled }
        override fun setStatusBarDisabled(disabled: Boolean) { call("status_bar"); statusBarOff = disabled }
        override fun setStayOn(value: Int) = call("stay_on")
        override fun addRestriction(name: String) { call("restriction:$name"); restrictions += name }
        override fun clearRestriction(name: String) { call("restriction:$name"); restrictions -= name }
        override fun setUninstallBlocked(blocked: Boolean) = call("uninstall_blocked")
        override fun setPersistentHome(on: Boolean) { call("persistent_home"); homePinned = on }
        override fun setHomeAlias(enabled: Boolean) { call("home_alias"); aliasOn = enabled }
        fun clean() = !keyguardOff && !statusBarOff && !homePinned && restrictions.isEmpty()
    }

    private var clock = 1_000_000L
    private fun runner(s: Mem = Mem()) = LockdownRunner(s) { clock }

    @Test fun aFailedEngageNeverBlocksARelease() {
        val state = Mem()
        val r = runner(state)
        val d = Dev(failing = mutableSetOf("keyguard"))
        assertEquals(listOf("keyguard"), r.sync(d, Mode.ENGAGE, false))
        assertTrue(d.statusBarOff)
        clock += 5_000 // well inside the 60 s back-off of the failed engage
        d.failing.clear()
        assertEquals("release runs at once", emptyList<String>(), r.sync(d, Mode.RELEASE, false))
        assertTrue(d.clean())
        assertFalse(state.engaged)
    }

    @Test fun anEngageBackOffStillHoldsBackAnotherEngage() {
        val r = runner()
        val d = Dev(failing = mutableSetOf("keyguard"))
        r.sync(d, Mode.ENGAGE, false)
        val calls = d.calls
        clock += 1_000
        assertNull(r.sync(d, Mode.ENGAGE, false))
        assertEquals(calls, d.calls)
        clock += 60_000
        assertEquals(listOf("keyguard"), r.sync(d, Mode.ENGAGE, false))
    }

    @Test fun aFailedReleaseIsRemembered() {
        val state = Mem()
        val r = runner(state)
        val d = Dev()
        r.sync(d, Mode.ENGAGE, false)
        d.failing += "status_bar"
        assertEquals(listOf("status_bar"), r.sync(d, Mode.RELEASE, false))
        assertTrue("something else must retry it", state.releasePending)
        assertTrue(state.engaged)
        d.failing.clear()
        assertEquals(emptyList<String>(), r.retryPendingRelease(d, false))
        assertFalse(state.releasePending)
        assertFalse(state.engaged)
        assertTrue(d.clean())
        assertNull("nothing pending any more", r.retryPendingRelease(d, false))
    }

    @Test fun releaseNowIgnoresBackOffAndTheAppliedCache() {
        val r = runner()
        val d = Dev(failing = mutableSetOf("keyguard"))
        r.sync(d, Mode.ENGAGE, false) // failed engage, in back-off
        d.failing.clear()
        assertEquals(emptyList<String>(), r.releaseNow(d, true, full = false))
        assertTrue(d.clean())
        assertTrue(d.aliasOn)
        // and again right away: runs again, not cached (the PIN exit twice in a row)
        d.aliasOn = false
        assertEquals(emptyList<String>(), r.releaseNow(d, true, full = false))
        assertTrue(d.aliasOn)
    }

    @Test fun aNeverEngagedTabletOnlyGetsTheCheapRelease() {
        val d = Dev()
        d.restrictions += "set_by_the_admin_another_way"
        d.keyguardOff = true
        val r = runner()
        assertEquals(emptyList<String>(), r.sync(d, Mode.RELEASE, homeMode = true))
        assertEquals(setOf("set_by_the_admin_another_way"), d.restrictions)
        assertTrue(d.keyguardOff)
        assertTrue(d.aliasOn)
    }

    @Test fun removingTheOwnerReleasesEvenIfNeverEngaged() {
        val d = Dev()
        d.keyguardOff = true
        runner().releaseNow(d, false, full = true)
        assertFalse(d.keyguardOff)
    }

    @Test fun aRestartedProcessReleasesWhatWasEngaged() {
        val state = Mem()
        val first = runner(state)
        val d = Dev()
        first.sync(d, Mode.ENGAGE, false)
        // the process dies; the stored state stays; a new runner (a new process) is asked to release at boot
        val second = runner(state)
        assertEquals(emptyList<String>(), second.sync(d, Mode.RELEASE, false))
        assertTrue(d.clean())
    }

    @Test fun anEngageCrashHalfwayIsStillMarkedEngaged() {
        val state = Mem()
        val d = Dev(failing = mutableSetOf("lock_task_packages", "keyguard"))
        runner(state).sync(d, Mode.ENGAGE, false)
        assertTrue(state.engaged)
    }

    @Test fun theBreakerTurnsEngageIntoReleaseUntilAManifestArrives() {
        val state = Mem()
        val r = runner(state)
        val d = Dev()
        r.sync(d, Mode.ENGAGE, false)
        assertFalse(d.clean())
        r.trip()
        assertEquals(Mode.RELEASE, r.effective(Mode.ENGAGE))
        assertEquals(emptyList<String>(), r.sync(d, Mode.ENGAGE, false))
        assertTrue(d.clean())
        assertNull("stays released", r.sync(d, Mode.ENGAGE, false))
        r.manifestFetched()
        assertEquals(Mode.ENGAGE, r.effective(Mode.ENGAGE))
        assertEquals(emptyList<String>(), r.sync(d, Mode.ENGAGE, false))
        assertFalse(d.clean())
    }

    @Test fun theBreakerSurvivesARestart() {
        val state = Mem().also { it.tripped = true }
        val d = Dev()
        assertEquals(emptyList<String>(), runner(state).sync(d, Mode.ENGAGE, false))
        assertTrue(d.clean())
    }

    @Test fun engageDoesNotSetPersistentHomeWhenTheAliasFailed() {
        val d = Dev(failing = mutableSetOf("home_alias"))
        val failed = Lockdown.engage(d)
        assertTrue("home_alias" in failed && "persistent_home" in failed)
        assertFalse(d.homePinned)
    }

    // ---- crash loop ----------------------------------------------------------------------

    @Test fun fiveStartsInTenMinutesTrip() {
        var starts = emptyList<Long>()
        val t0 = 10_000_000L
        for (i in 0 until 4) starts = CrashLoop.record(starts, t0 + i * 60_000L)
        assertFalse(CrashLoop.tripped(starts, t0 + 3 * 60_000L))
        starts = CrashLoop.record(starts, t0 + 4 * 60_000L)
        assertTrue(CrashLoop.tripped(starts, t0 + 4 * 60_000L))
    }

    @Test fun slowStartsDoNotTripAndAClockSetBackIsHarmless() {
        var starts = emptyList<Long>()
        for (i in 0 until 8) starts = CrashLoop.record(starts, i * 11 * 60_000L)
        assertFalse(CrashLoop.tripped(starts, 7 * 11 * 60_000L))
        // clock set far back: the future entries are dropped, not counted
        val back = CrashLoop.record(starts, 1_000L)
        assertEquals(listOf(1_000L), back)
    }

    @Test fun healthRequiresAProcessThatLivedAWhile() {
        assertFalse(CrashLoop.healthy(10_000))
        assertTrue(CrashLoop.healthy(60_000))
    }

    @Test fun startListRoundTrips() {
        assertEquals(listOf(1L, 2L, 3L), CrashLoop.parse(CrashLoop.format(listOf(1L, 2L, 3L))))
        assertEquals(emptyList<Long>(), CrashLoop.parse(null))
        assertEquals(emptyList<Long>(), CrashLoop.parse("x,,y"))
    }
}
