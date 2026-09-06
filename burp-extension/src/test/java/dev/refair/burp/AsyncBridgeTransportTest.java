package dev.refair.burp;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AsyncBridgeTransportTest {
    @Test
    void fullQueueDropsAndLogsWithoutBlockingProducer() throws Exception {
        CountDownLatch firstSendStarted = new CountDownLatch(1);
        CountDownLatch releaseSender = new CountDownLatch(1);
        CountDownLatch twoSent = new CountDownLatch(2);
        List<String> errors = new CopyOnWriteArrayList<>();
        EventSender sender = exchange -> {
            firstSendStarted.countDown();
            releaseSender.await();
            twoSent.countDown();
        };
        BridgeLogger logger = logger(errors);

        try (AsyncBridgeTransport transport =
                     new AsyncBridgeTransport(1, sender, logger)) {
            assertTrue(transport.enqueue(exchange(1)));
            assertTrue(firstSendStarted.await(2, TimeUnit.SECONDS));
            assertTrue(transport.enqueue(exchange(2)));
            assertFalse(transport.enqueue(exchange(3)));
            assertTrue(errors.stream().anyMatch(message -> message.contains("queue is full")));
            releaseSender.countDown();
            assertTrue(twoSent.await(2, TimeUnit.SECONDS));
        }
    }

    @Test
    void oversizedExchangeIsDroppedAndLogged() {
        List<String> errors = new CopyOnWriteArrayList<>();
        try (AsyncBridgeTransport transport = new AsyncBridgeTransport(
                exchange -> {
                    throw new AssertionError("oversized event must not be sent");
                }, logger(errors))) {
            PassiveExchange oversized = new PassiveExchange(
                    8082,
                    Instant.now(),
                    "GET",
                    "https://example.test/",
                    200,
                    new byte[AsyncBridgeTransport.MAX_EXCHANGE_BYTES + 1],
                    new byte[0]);

            assertFalse(transport.enqueue(oversized));
            assertTrue(errors.stream().anyMatch(message -> message.contains("exceed")));
        }
    }

    private static PassiveExchange exchange(int marker) {
        return new PassiveExchange(
                8082,
                Instant.now(),
                "GET",
                "https://example.test/" + marker,
                200,
                new byte[]{(byte) marker},
                new byte[]{(byte) marker});
    }

    private static BridgeLogger logger(List<String> errors) {
        return new BridgeLogger() {
            @Override
            public void info(String message) {
            }

            @Override
            public void error(String message) {
                errors.add(message);
            }
        };
    }
}
