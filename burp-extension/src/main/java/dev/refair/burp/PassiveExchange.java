package dev.refair.burp;

import java.time.Instant;
import java.util.Base64;
import java.util.Objects;

record PassiveExchange(
        int listenerPort,
        Instant observedAt,
        String method,
        String url,
        int responseStatus,
        byte[] rawRequest,
        byte[] rawResponse) {

    PassiveExchange {
        Objects.requireNonNull(observedAt);
        Objects.requireNonNull(method);
        Objects.requireNonNull(url);
        rawRequest = Objects.requireNonNull(rawRequest).clone();
        rawResponse = Objects.requireNonNull(rawResponse).clone();
    }

    @Override
    public byte[] rawRequest() {
        return rawRequest.clone();
    }

    @Override
    public byte[] rawResponse() {
        return rawResponse.clone();
    }

    int rawSize() {
        return rawRequest.length + rawResponse.length;
    }

    String toJson() {
        return "{" +
                "\"listener_port\":" + listenerPort + "," +
                "\"observed_at\":" + quote(observedAt.toString()) + "," +
                "\"method\":" + quote(method) + "," +
                "\"url\":" + quote(url) + "," +
                "\"response_status\":" + responseStatus + "," +
                "\"raw_request_base64\":" +
                quote(Base64.getEncoder().encodeToString(rawRequest)) + "," +
                "\"raw_response_base64\":" +
                quote(Base64.getEncoder().encodeToString(rawResponse)) +
                "}";
    }

    private static String quote(String value) {
        StringBuilder escaped = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> escaped.append("\\\"");
                case '\\' -> escaped.append("\\\\");
                case '\b' -> escaped.append("\\b");
                case '\f' -> escaped.append("\\f");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                default -> {
                    if (character < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) character));
                    } else {
                        escaped.append(character);
                    }
                }
            }
        }
        return escaped.append('"').toString();
    }
}
