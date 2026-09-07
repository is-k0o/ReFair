package dev.refair.burp;

import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HttpCollectorSenderTest {
    @Test
    void sendsPlainHttpUsingHttp11WithoutUpgradeNegotiation() throws Exception {
        AtomicReference<String> receivedHeaders = new AtomicReference<>();
        try (ServerSocket server = new ServerSocket(
                0, 1, InetAddress.getByName("127.0.0.1"))) {
            Thread serverThread = Thread.ofPlatform().start(() -> {
                try (Socket socket = server.accept()) {
                    String headers = readHeaders(socket.getInputStream());
                    receivedHeaders.set(headers);
                    int contentLength = contentLength(headers);
                    socket.getInputStream().readNBytes(contentLength);
                    OutputStream output = socket.getOutputStream();
                    output.write(("HTTP/1.1 201 Created\r\n" +
                            "Content-Length: 0\r\n" +
                            "Connection: close\r\n\r\n")
                            .getBytes(StandardCharsets.US_ASCII));
                    output.flush();
                } catch (Exception error) {
                    throw new RuntimeException(error);
                }
            });

            URI collector = URI.create("http://127.0.0.1:" +
                    server.getLocalPort() + "/v1/observations/passive");
            new HttpCollectorSender(collector).send(exchange());
            serverThread.join(2000);
            assertFalse(serverThread.isAlive());
        }

        String headers = receivedHeaders.get();
        assertTrue(headers.startsWith(
                "POST /v1/observations/passive HTTP/1.1\r\n"));
        String lowerHeaders = headers.toLowerCase(Locale.ROOT);
        assertFalse(lowerHeaders.contains("upgrade:"));
        assertFalse(lowerHeaders.contains("http2-settings:"));
    }

    private static String readHeaders(InputStream input) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        int matched = 0;
        byte[] terminator = "\r\n\r\n".getBytes(StandardCharsets.US_ASCII);
        while (matched < terminator.length) {
            int next = input.read();
            if (next < 0) {
                throw new IllegalStateException("connection closed before HTTP headers");
            }
            output.write(next);
            matched = next == terminator[matched] ? matched + 1 : 0;
            if (output.size() > 64 * 1024) {
                throw new IllegalStateException("HTTP headers exceeded test limit");
            }
        }
        return output.toString(StandardCharsets.US_ASCII);
    }

    private static int contentLength(String headers) {
        for (String line : headers.split("\r\n")) {
            if (line.toLowerCase(Locale.ROOT).startsWith("content-length:")) {
                return Integer.parseInt(line.substring(line.indexOf(':') + 1).trim());
            }
        }
        return 0;
    }

    private static PassiveExchange exchange() {
        return new PassiveExchange(
                8082,
                Instant.parse("2026-09-07T12:00:00Z"),
                "GET",
                "https://example.test/refair-http11-test",
                200,
                "GET /refair-http11-test HTTP/2\r\n\r\n"
                        .getBytes(StandardCharsets.US_ASCII),
                "HTTP/2 200\r\n\r\n".getBytes(StandardCharsets.US_ASCII));
    }
}
