package dev.refair.burp;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Base64;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PassiveExchangeTest {
    @Test
    void serializesRawBytesAsBase64WithoutTextConversion() {
        byte[] request = new byte[]{0, 1, -1, 13, 10};
        byte[] response = new byte[]{-2, 0, 127};
        PassiveExchange exchange = new PassiveExchange(
                8082,
                Instant.parse("2026-09-06T12:34:56Z"),
                "PO\"ST",
                "https://example.test/a?value=\\x",
                201,
                request,
                response);

        String json = exchange.toJson();

        assertTrue(json.contains("\"listener_port\":8082"));
        assertTrue(json.contains("\"method\":\"PO\\\"ST\""));
        assertTrue(json.contains(Base64.getEncoder().encodeToString(request)));
        assertTrue(json.contains(Base64.getEncoder().encodeToString(response)));
        assertArrayEquals(request, exchange.rawRequest());
        assertArrayEquals(response, exchange.rawResponse());
    }
}
