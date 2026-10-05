package io.cluecdc.connect;

import com.amazonaws.auth.AWSCredentials;
import com.amazonaws.auth.AWSCredentialsProvider;
import com.amazonaws.auth.BasicSessionCredentials;
import java.util.Map;
import org.apache.kafka.common.Configurable;
import org.apache.kafka.common.config.ConfigException;

/** Supplies short-lived AWS credentials after Kafka ConfigProvider resolution. */
public final class ClueSessionCredentialsProvider
        implements AWSCredentialsProvider, Configurable {
    private volatile AWSCredentials credentials;

    @Override
    public void configure(Map<String, ?> configs) {
        String accessKey = value(configs, "cluecdc.access.key");
        String secretKey = value(configs, "cluecdc.secret.key");
        String sessionToken = value(configs, "cluecdc.session.token");
        credentials = new BasicSessionCredentials(accessKey, secretKey, sessionToken);
    }

    private static String value(Map<String, ?> configs, String key) {
        Object value = configs.get(key);
        if (value == null || value.toString().isBlank()) {
            throw new ConfigException("ClueCDC temporary AWS credentials are incomplete");
        }
        return value.toString();
    }

    @Override
    public AWSCredentials getCredentials() {
        if (credentials == null) {
            throw new ConfigException("ClueCDC temporary AWS credentials are not configured");
        }
        return credentials;
    }

    @Override
    public void refresh() {
        // ConfigProvider refreshes require a connector reconfiguration.
    }
}
