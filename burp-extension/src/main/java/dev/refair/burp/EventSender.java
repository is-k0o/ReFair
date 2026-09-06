package dev.refair.burp;

@FunctionalInterface
interface EventSender {
    void send(PassiveExchange exchange) throws Exception;
}
