package dev.refair.burp;

import java.util.Objects;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.atomic.AtomicBoolean;

final class AsyncBridgeTransport implements AutoCloseable {
    static final int DEFAULT_QUEUE_CAPACITY = 32;
    static final int MAX_EXCHANGE_BYTES = 16 * 1024 * 1024;

    private final ArrayBlockingQueue<PassiveExchange> queue;
    private final EventSender sender;
    private final BridgeLogger logger;
    private final AtomicBoolean running = new AtomicBoolean(true);
    private final Thread worker;

    AsyncBridgeTransport(EventSender sender, BridgeLogger logger) {
        this(DEFAULT_QUEUE_CAPACITY, sender, logger);
    }

    AsyncBridgeTransport(int capacity, EventSender sender, BridgeLogger logger) {
        if (capacity < 1) {
            throw new IllegalArgumentException("queue capacity must be positive");
        }
        this.queue = new ArrayBlockingQueue<>(capacity);
        this.sender = Objects.requireNonNull(sender);
        this.logger = Objects.requireNonNull(logger);
        this.worker = Thread.ofPlatform()
                .daemon(true)
                .name("refair-passive-transport")
                .start(this::runWorker);
    }

    boolean enqueue(PassiveExchange exchange) {
        if (!running.get()) {
            logger.error("Dropped passive exchange: transport is closed");
            return false;
        }
        if (exchange.rawSize() > MAX_EXCHANGE_BYTES) {
            logger.error("Dropped passive exchange: raw request/response exceed " +
                    MAX_EXCHANGE_BYTES + " bytes");
            return false;
        }
        if (!queue.offer(exchange)) {
            logger.error("Dropped passive exchange: bounded transport queue is full");
            return false;
        }
        return true;
    }

    private void runWorker() {
        while (running.get()) {
            PassiveExchange exchange = null;
            try {
                exchange = queue.take();
                sender.send(exchange);
            } catch (InterruptedException error) {
                if (exchange != null) {
                    logger.error("Dropped in-flight passive exchange while " +
                            "unloading extension");
                }
                Thread.currentThread().interrupt();
                return;
            } catch (Exception error) {
                logger.error("Passive collector transport failed; event dropped: " +
                        error.getMessage());
            }
        }
    }

    @Override
    public void close() {
        if (!running.compareAndSet(true, false)) {
            return;
        }
        int dropped = queue.size();
        queue.clear();
        if (dropped > 0) {
            logger.error("Dropped " + dropped +
                    " queued passive exchange(s) while unloading extension");
        }
        worker.interrupt();
    }
}
