package dev.refair.burp;

import java.util.OptionalInt;

final class ListenerPort {
    private ListenerPort() {
    }

    static OptionalInt parse(String listenerInterface) {
        if (listenerInterface == null) {
            return OptionalInt.empty();
        }
        int separator = listenerInterface.lastIndexOf(':');
        if (separator < 0 || separator == listenerInterface.length() - 1) {
            return OptionalInt.empty();
        }
        try {
            int port = Integer.parseInt(listenerInterface.substring(separator + 1));
            return port >= 1 && port <= 65535
                    ? OptionalInt.of(port)
                    : OptionalInt.empty();
        } catch (NumberFormatException error) {
            return OptionalInt.empty();
        }
    }
}
