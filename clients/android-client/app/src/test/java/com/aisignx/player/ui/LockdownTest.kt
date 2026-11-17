package com.aisignx.player.ui

import android.os.UserManager
import com.aisignx.player.ui.Lockdown.Mode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LockdownTest {

    /** A pretend device: keeps the state that the policy calls leave behind. */
    private class Fake(var stayOn: Int? = 0, val failing: Set<String> = emptySet()) : Lockdown.Ops {
        override val packageName = "com.aisignx.player"
        override var stayOnBackup: Int? = null
        var pkgs = listOf<String>()
        var features = 0
        var keyguardOff = false
        var statusBarOff = false
        val restrictions = mutableSetOf<String>()
        var uninstallOff = false
        var homePinned = false
        var aliasOn = false

        private fun call(name: String) { if (name in failing) throw IllegalStateException(name) }
        override fun currentStayOn() = stayOn
        override fun setLockTaskPackages(packages: List<String>) { call("lock_task_packages"); pkgs = packages }
        override fun setLockTaskFeatures(features: Int) { call("lock_task_features"); this.features = features }
        override fun setKeyguardDisabled(disabled: Boolean) { call("keyguard"); keyguardOff = disabled }
        override fun setStatusBarDisabled(disabled: Boolean) { call("status_bar"); statusBarOff = disabled }
        override fun setStayOn(value: Int) { call("stay_on"); stayOn = value }
        override fun addRestriction(name: String) { call("restriction:$name"); restrictions += name }
        override fun clearRestriction(name: String) { call("restriction:$name"); restrictions -= name }
        override fun setUninstallBlocked(blocked: Boolean) { call("uninstall_blocked"); uninstallOff = blocked }
        override fun setPersistentHome(on: Boolean) { call("persistent_home"); homePinned = on }
        override fun setHomeAlias(enabled: Boolean) { call("home_alias"); aliasOn = enabled }
    }

    // ---- what to do for which inputs ---------------------------------------------------------

    @Test fun decisionTable() {
        for (lock in listOf(true, false)) for (unlocked in listOf(true, false)) {
            assertEquals("not Device Owner: never touch device policy", Mode.NONE, Lockdown.decide(false, lock, unlocked))
        }
        assertEquals(Mode.ENGAGE, Lockdown.decide(owner = true, kioskLock = true, remoteUnlocked = false))
        assertEquals(Mode.RELEASE, Lockdown.decide(owner = true, kioskLock = false, remoteUnlocked = false))
        assertEquals(Mode.RELEASE, Lockdown.decide(owner = true, kioskLock = true, remoteUnlocked = true))
        assertEquals(Mode.RELEASE, Lockdown.decide(owner = true, kioskLock = false, remoteUnlocked = true))
    }

    @Test fun theRestrictionSetIsSmallAndDocumented() {
        assertEquals(
            setOf(UserManager.DISALLOW_SAFE_BOOT, UserManager.DISALLOW_ADD_USER, UserManager.DISALLOW_FACTORY_RESET, UserManager.DISALLOW_USB_FILE_TRANSFER),
            Lockdown.RESTRICTIONS.toSet(),
        )
        assertFalse("debugging is not restricted (but adb is not relied on as a repair path either)", UserManager.DISALLOW_DEBUGGING_FEATURES in Lockdown.RESTRICTIONS)
        assertEquals(7, Lockdown.STAY_ON_ALWAYS)
    }

    @Test fun lockTaskFeaturesKeepOnlyThePowerMenu() {
        val f = Lockdown.LOCK_TASK_FEATURES
        assertEquals(android.app.admin.DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS, f)
        // SYSTEM_INFO 1, NOTIFICATIONS 2, HOME 4, OVERVIEW 8, KEYGUARD 32 are all off.
        for (bit in listOf(1, 2, 4, 8, 32)) assertEquals("feature $bit", 0, f and bit)
    }

    // ---- engage ---------------------------------------------------------------------------

    @Test fun engageAppliesEverything() {
        val d = Fake(stayOn = 0)
        assertTrue(Lockdown.engage(d).isEmpty())
        assertEquals(listOf("com.aisignx.player"), d.pkgs)
        assertEquals(Lockdown.LOCK_TASK_FEATURES, d.features)
        assertTrue(d.keyguardOff)
        assertTrue(d.statusBarOff)
        assertEquals(7, d.stayOn)
        assertEquals(Lockdown.RESTRICTIONS.toSet(), d.restrictions)
        assertTrue(d.uninstallOff)
        assertTrue(d.homePinned)
        assertTrue(d.aliasOn)
    }

    @Test fun engageTwiceIsTheSameAsOnce() {
        val twice = Fake(stayOn = 3).also { Lockdown.engage(it); Lockdown.engage(it) }
        assertEquals(Lockdown.RESTRICTIONS.toSet(), twice.restrictions)
        assertEquals(7, twice.stayOn)
        assertEquals("the second run must not save the lockdown's own value", 3, twice.stayOnBackup)
    }

    // ---- release: the web page can always undo it -------------------------------------------------

    @Test fun releaseUndoesEverythingEngageDid() {
        val d = Fake(stayOn = 2)
        Lockdown.engage(d)
        assertTrue(Lockdown.release(d, homeMode = false).isEmpty())
        assertEquals(emptyList<String>(), d.pkgs)
        assertFalse(d.keyguardOff)
        assertFalse(d.statusBarOff)
        assertEquals("the value from before", 2, d.stayOn)
        assertNull(d.stayOnBackup)
        assertTrue("factory reset is possible again", d.restrictions.isEmpty())
        assertFalse(d.uninstallOff)
        assertFalse(d.homePinned)
        assertFalse(d.aliasOn)
    }

    @Test fun releaseKeepsTheUsersHomeModeChoice() {
        val d = Fake().also { Lockdown.engage(it) }
        Lockdown.release(d, homeMode = true)
        assertTrue(d.aliasOn)
        assertFalse(d.homePinned)
    }

    @Test fun releaseIsRepeatableAndHarmlessWhenNothingWasEngaged() {
        val d = Fake(stayOn = 5)
        assertTrue(Lockdown.release(d, false).isEmpty())
        assertTrue(Lockdown.release(d, false).isEmpty())
        assertEquals("not overwritten: nothing was saved", 5, d.stayOn)
        assertTrue(d.restrictions.isEmpty())
    }

    @Test fun engageReleaseCyclesEndClean() {
        val d = Fake(stayOn = 1)
        repeat(3) { Lockdown.engage(d); Lockdown.release(d, false) }
        assertEquals(1, d.stayOn)
        assertTrue(d.restrictions.isEmpty())
        assertFalse(d.statusBarOff)
        assertFalse(d.keyguardOff)
    }

    @Test fun aFailingStepNeverStopsTheOthers() {
        val d = Fake(failing = setOf("keyguard"))
        assertEquals(listOf("keyguard"), Lockdown.engage(d))
        assertTrue(d.statusBarOff)
        assertEquals(Lockdown.RESTRICTIONS.toSet(), d.restrictions)

        val broken = setOf("status_bar", "restriction:${UserManager.DISALLOW_SAFE_BOOT}")
        val d2 = Fake()
        Lockdown.engage(d2)
        val d3 = Fake(failing = broken).also { it.restrictions += d2.restrictions; it.statusBarOff = true; it.keyguardOff = true
            it.homePinned = true; it.pkgs = listOf("com.aisignx.player") }
        val failed = Lockdown.release(d3, false)
        assertEquals(broken, failed.toSet())
        assertFalse(d3.keyguardOff)
        assertFalse(d3.homePinned)
        assertEquals(emptyList<String>(), d3.pkgs)
        assertFalse(UserManager.DISALLOW_FACTORY_RESET in d3.restrictions)
    }

    @Test fun retryAfterAFailureIsRateLimited() {
        assertTrue(Lockdown.retryAllowed(0, 5_000))
        assertFalse(Lockdown.retryAllowed(1_000, 30_000))
        assertTrue(Lockdown.retryAllowed(1_000, 61_000))
        assertTrue(Lockdown.retryAllowed(90_000, 1_000))
    }
}
