package dev.refair.burp;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ListenerPortTest {
    @Test
    void parsesDocumentedBurpListenerDisplayFormats() {
        assertEquals(8082, ListenerPort.parse("127.0.0.1:8082").orElseThrow());
        assertEquals(8083, ListenerPort.parse("[::1]:8083").orElseThrow());
    }

    @Test
    void rejectsMalformedOrOutOfRangePorts() {
        assertTrue(ListenerPort.parse("127.0.0.1").isEmpty());
        assertTrue(ListenerPort.parse("127.0.0.1:not-a-port").isEmpty());
        assertTrue(ListenerPort.parse("127.0.0.1:0").isEmpty());
        assertTrue(ListenerPort.parse("127.0.0.1:65536").isEmpty());
    }
}
