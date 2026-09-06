package dev.refair.burp;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

final class HttpCollectorSender implements EventSender {
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(2);

    private final URI collectorUri;
    private final HttpClient client;

    HttpCollectorSender(URI collectorUri) {
        this.collectorUri = collectorUri;
        this.client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(1))
                .build();
    }

    @Override
    public void send(PassiveExchange exchange) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(collectorUri)
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(
                        exchange.toJson(), StandardCharsets.UTF_8))
                .build();
        HttpResponse<Void> response = client.send(
                request, HttpResponse.BodyHandlers.discarding());
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IOException("collector returned HTTP " + response.statusCode());
        }
    }
}
