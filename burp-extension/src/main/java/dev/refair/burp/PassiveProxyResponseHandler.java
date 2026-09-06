package dev.refair.burp;

import burp.api.montoya.http.message.requests.HttpRequest;
import burp.api.montoya.proxy.http.InterceptedResponse;
import burp.api.montoya.proxy.http.ProxyResponseHandler;
import burp.api.montoya.proxy.http.ProxyResponseReceivedAction;
import burp.api.montoya.proxy.http.ProxyResponseToBeSentAction;

import java.time.Instant;
import java.util.OptionalInt;

final class PassiveProxyResponseHandler implements ProxyResponseHandler {
    private final AsyncBridgeTransport transport;
    private final BridgeLogger logger;

    PassiveProxyResponseHandler(AsyncBridgeTransport transport, BridgeLogger logger) {
        this.transport = transport;
        this.logger = logger;
    }

    @Override
    public ProxyResponseReceivedAction handleResponseReceived(
            InterceptedResponse interceptedResponse) {
        try {
            OptionalInt listenerPort = ListenerPort.parse(
                    interceptedResponse.listenerInterface());
            if (listenerPort.isEmpty()) {
                logger.error("Dropped passive exchange: could not parse Burp listener " +
                        interceptedResponse.listenerInterface());
            } else {
                HttpRequest request = interceptedResponse.initiatingRequest();
                transport.enqueue(new PassiveExchange(
                        listenerPort.getAsInt(),
                        Instant.now(),
                        request.method(),
                        request.url(),
                        interceptedResponse.statusCode(),
                        request.toByteArray().getBytes(),
                        interceptedResponse.toByteArray().getBytes()));
            }
        } catch (RuntimeException error) {
            logger.error("Dropped passive exchange during serialization: " +
                    error.getMessage());
        }
        return ProxyResponseReceivedAction.continueWith(interceptedResponse);
    }

    @Override
    public ProxyResponseToBeSentAction handleResponseToBeSent(
            InterceptedResponse interceptedResponse) {
        return ProxyResponseToBeSentAction.continueWith(interceptedResponse);
    }
}
