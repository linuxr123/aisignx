package com.aisignx.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerUrlTest {

    @Test fun addsHttpWhenNoScheme() {
        assertEquals("http://192.168.1.20:5000", ServerUrl.normalize("192.168.1.20:5000"))
        assertEquals("http://localhost:5000", ServerUrl.normalize(" localhost:5000/ "))
    }

    @Test fun keepsHttpsAndDropsDefaultPorts() {
        assertEquals("https://signage.example.lan", ServerUrl.normalize("https://signage.example.lan/"))
        assertEquals("https://signage.example.lan", ServerUrl.normalize("HTTPS://Signage.Example.LAN:443"))
        assertEquals("http://host", ServerUrl.normalize("http://host:80"))
        assertEquals("https://host:5000", ServerUrl.normalize("https://host:5000"))
    }

    @Test fun noPortRewritingOrForcedHttps() {
        assertEquals("http://10.0.0.5:5000", ServerUrl.normalize("http://10.0.0.5:5000"))
    }

    @Test fun ipv6Literal() {
        assertEquals("http://[::1]:5000", ServerUrl.normalize("http://[::1]:5000"))
    }

    @Test fun rejectsBadInput() {
        assertNull(ServerUrl.normalize(""))
        assertNull(ServerUrl.normalize("   "))
        assertNull(ServerUrl.normalize(null))
        assertNull(ServerUrl.normalize("ftp://host"))
        assertNull(ServerUrl.normalize("javascript://host"))
        assertNull(ServerUrl.normalize("http://user:pw@host"))
        assertNull(ServerUrl.normalize("http://host/admin"))
        assertNull(ServerUrl.normalize("http://host/?x=1"))
        assertNull(ServerUrl.normalize("http://host?x=1"))
        assertNull(ServerUrl.normalize("http://host#frag"))
        assertNull(ServerUrl.normalize("http://host:99999"))
        assertNull(ServerUrl.normalize("http://exa mple"))
        assertNull(ServerUrl.normalize("http://"))
    }

    @Test fun sameOriginComparesSchemeHostPort() {
        assertTrue(ServerUrl.sameOrigin("http://h:5000/player/", "http://h:5000"))
        assertTrue(ServerUrl.sameOrigin("https://H/x", "https://h"))
        assertTrue(ServerUrl.sameOrigin("https://h:443/x", "https://h"))
        assertFalse(ServerUrl.sameOrigin("http://h:5001/", "http://h:5000"))
        assertFalse(ServerUrl.sameOrigin("https://h:5000/", "http://h:5000"))
        assertFalse(ServerUrl.sameOrigin("http://evil/", "http://h"))
    }

    @Test fun privateHosts() {
        listOf("192.168.1.20", "10.1.2.3", "172.16.0.1", "172.31.255.255", "127.0.0.1", "169.254.1.1",
            "localhost", "signage", "box.local", "srv.lan", "a.home.arpa", "::1", "fd00::1", "fe80::1", "[::1]")
            .forEach { assertTrue(it, ServerUrl.isPrivateHost(it)) }
        listOf("8.8.8.8", "172.32.0.1", "example.com", "signage.example.org", "2001:db8::1", "")
            .forEach { assertFalse(it, ServerUrl.isPrivateHost(it)) }
    }

    @Test fun hostOf() {
        assertEquals("192.168.1.20", ServerUrl.hostOf("http://192.168.1.20:5000"))
        assertEquals("::1", ServerUrl.hostOf("http://[::1]:5000"))
    }
}
