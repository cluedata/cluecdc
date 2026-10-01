package io.cluecdc.connect;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import org.apache.kafka.common.config.ConfigData;
import org.apache.kafka.common.config.ConfigException;
import org.apache.kafka.common.config.provider.ConfigProvider;

/** Resolves encrypted control-plane secrets without persisting passwords in Connect topics. */
public final class ClueSecretConfigProvider implements ConfigProvider {
    private static final Set<String> ALLOWED_KEYS = Set.of(
            "password", "access_key", "secret_key", "session_token",
            "credential", "token", "client_secret");
    private String baseUrl;
    private String token;

    @Override
    public void configure(Map<String, ?> configs) {
        Object configuredUrl = configs.get("url");
        baseUrl = configuredUrl == null ? "http://cluecdc-api:8000" : configuredUrl.toString();
        URI origin = URI.create(baseUrl);
        if ((!"http".equals(origin.getScheme()) && !"https".equals(origin.getScheme()))
                || origin.getHost() == null || origin.getUserInfo() != null) {
            throw new ConfigException("Invalid ClueCDC secret service origin");
        }
        token = System.getenv("CLUECDC_SECRETS_TOKEN");
        if (token == null || token.length() < 32) {
            throw new ConfigException("ClueCDC secret resolver requires a service token");
        }
    }

    @Override
    public ConfigData get(String path) {
        return get(path, Set.of("password"));
    }

    @Override
    public ConfigData get(String path, Set<String> keys) {
        if (path == null || !path.matches("[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}")
                || keys.isEmpty() || !ALLOWED_KEYS.containsAll(keys)) {
            throw new ConfigException("Invalid ClueCDC secret reference");
        }
        Map<String, String> values = new HashMap<>();
        for (String key : keys) {
            values.put(key, resolve(path, key));
        }
        return new ConfigData(Map.copyOf(values));
    }

    private String resolve(String path, String key) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) URI.create(
                    baseUrl + "/internal/secrets/" + path + "?key=" + key).toURL().openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            if (connection.getResponseCode() != 200) {
                throw new ConfigException("ClueCDC secret resolution was rejected");
            }
            byte[] data;
            try (var stream = connection.getInputStream()) {
                data = stream.readNBytes(65537);
            }
            if (data.length > 65536) throw new ConfigException("ClueCDC secret exceeds size limit");
            return new String(data, StandardCharsets.UTF_8);
        } catch (IOException e) {
            // Never retain upstream exception text or response bodies in worker logs.
            throw new ConfigException("ClueCDC secret service is unavailable");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    @Override
    public void close() {
        token = null;
    }
}
