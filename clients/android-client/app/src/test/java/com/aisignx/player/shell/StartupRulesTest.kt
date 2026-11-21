package com.aisignx.player.shell

import com.aisignx.player.shell.StartupRules.Decision
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StartupRulesTest {
    @Test fun noModeLeavesHomeAloneInEveryState() {
        assertEquals(Decision.Apply(null), StartupRules.decide(null, lockKeepsHome = false))
        assertEquals(Decision.Apply(null), StartupRules.decide(null, lockKeepsHome = true))
    }

    @Test fun shellTurnsHomeModeOnAlsoWhenLockPlayerIsOn() {
        assertEquals(Decision.Apply(true), StartupRules.decide("shell", lockKeepsHome = false))
        assertEquals(Decision.Apply(true), StartupRules.decide("shell", lockKeepsHome = true))
    }

    @Test fun appTurnsHomeModeOffOnlyWhenLockPlayerIsOff() {
        assertEquals(Decision.Apply(false), StartupRules.decide("app", lockKeepsHome = false))
        val d = StartupRules.decide("app", lockKeepsHome = true)
        assertEquals(Decision.Refuse("Lock player is on: it keeps this app as the Home app. Turn Lock player off first"), d)
    }

    @Test fun unknownModeIsRefused() {
        assertTrue(StartupRules.decide("kiosk", false) is Decision.Refuse)
    }

    @Test fun reportedModeIsShellWhenTheAppIsTheHomeApp() {
        assertEquals("app", StartupRules.reportedMode(homeMode = false, lockdownEngaged = false))
        assertEquals("shell", StartupRules.reportedMode(homeMode = true, lockdownEngaged = false))
        assertEquals("shell", StartupRules.reportedMode(homeMode = false, lockdownEngaged = true))
        assertEquals("shell", StartupRules.reportedMode(homeMode = true, lockdownEngaged = true))
    }

    @Test fun doneTextNamesAutostartAndMode() {
        assertEquals("autostart on, mode shell", StartupRules.doneText(true, "shell"))
        assertEquals("autostart off, mode app", StartupRules.doneText(false, "app"))
    }

    @Test fun refusalTextFitsTheAckLimit() {
        assertTrue(StartupRules.LOCK_KEEPS_HOME.length <= 200)
    }
}
