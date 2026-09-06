package dev.refair.burp;

import burp.api.montoya.BurpExtension;
import burp.api.montoya.MontoyaApi;

import java.net.URI;

public final class ReFairExtension implements BurpExtension {
    private static final String DEFAULT_COLLECTOR_URL =
            "http://127.0.0.1:8765/v1/observations/passive";

    @Override
    public void initialize(MontoyaApi api) {
        api.extension().setName("ReFair Passive Bridge");
        BridgeLogger logger = new BridgeLogger() {
            @Override
            public void info(String message) {
                api.logging().logToOutput(message);
            }

            @Override
            public void error(String message) {
                api.logging().logToError(message);
            }
        };

        URI collectorUri = URI.create(System.getProperty(
                "refair.collector.url", DEFAULT_COLLECTOR_URL));
        AsyncBridgeTransport transport = new AsyncBridgeTransport(
                new HttpCollectorSender(collectorUri), logger);
        api.proxy().registerResponseHandler(
                new PassiveProxyResponseHandler(transport, logger));
        api.extension().registerUnloadingHandler(transport::close);
        logger.info("ReFair passive bridge loaded; collector=" + collectorUri);
    }
}
