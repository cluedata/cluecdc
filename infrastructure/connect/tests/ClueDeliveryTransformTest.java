package io.cluecdc.connect;

import java.util.Map;
import java.util.HashMap;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.source.SourceRecord;

/** Minimal connector-level contract test run while building the Connect image. */
public final class ClueDeliveryTransformTest {
    private static final String TOPIC = "capture.public.cars";
    private static final String COLUMNS = "[{\"name\":\"car_code\",\"logical_type\":\"STRING\","
        + "\"type\":\"text\",\"nullable\":false},{\"name\":\"model\","
        + "\"logical_type\":\"STRING\",\"type\":\"text\",\"nullable\":false},"
        + "{\"name\":\"updated_at\",\"logical_type\":\"TIMESTAMP\","
        + "\"type\":\"timestamp(6)\",\"nullable\":false}]";

    private static SourceRecord record(String operation, Object before, Object after) {
        Map<String, Object> value = new HashMap<>();
        value.put("before", before);
        value.put("after", after);
        value.put("source", Map.of("schema", "public", "table", "cars"));
        value.put("op", operation);
        value.put("ts_ms", 1L);
        return new SourceRecord(
            Map.of("source", "test"), Map.of("offset", 1), TOPIC, 0,
            null, Map.of("car_code", "CAR-1"), null, value
        );
    }

    private static ClueDeliveryTransform<SourceRecord> transform(String keyField) {
        String metadata = "{\"" + TOPIC + "\":{\"columns\":" + COLUMNS
            + ",\"effective_keys\":[\"" + keyField + "\"],"
            + "\"primary_keys\":[\"legacy_id\"],\"schema_name\":\"public\","
            + "\"table_name\":\"cars\"}}";
        ClueDeliveryTransform<SourceRecord> transform = new ClueDeliveryTransform<>();
        transform.configure(Map.of("metadata", metadata));
        return transform;
    }

    public static void main(String[] args) {
        ClueDeliveryTransform<SourceRecord> transform = transform("car_code");
        SourceRecord created = transform.apply(
            record("c", null, Map.of(
                "car_code", "CAR-1", "model", "Roadster",
                "updated_at", "2026-10-22T08:26:51.851873Z"
            ))
        );
        Struct key = (Struct) created.key();
        if (!"CAR-1".equals(key.getString("car_code"))) {
            throw new AssertionError("effective key was not reconstructed");
        }
        if (key.schema().field("legacy_id") != null) {
            throw new AssertionError("legacy primary key overrode explicit effective key");
        }
        Struct after = ((Struct) created.value()).getStruct("after");
        if (!"2026-10-22T08:26:51.851873Z".equals(after.getString("updated_at"))) {
            throw new AssertionError("MySQL TIMESTAMP string was not preserved");
        }
        SourceRecord deleted = transform.apply(
            record("d", Map.of(
                "car_code", "CAR-1", "model", "Roadster",
                "updated_at", "2026-10-22T08:26:51.851873Z"
            ), null)
        );
        if (deleted == null || deleted.value() == null) {
            throw new AssertionError("delete envelope was not preserved");
        }
        SourceRecord keyChanged = record("d", Map.of(
            "car_code", "CAR-1", "model", "Roadster",
            "updated_at", "2026-10-22T08:26:51.851873Z"
        ), null);
        keyChanged.headers().add("__debezium.oldkey", Map.of("car_code", "CAR-1"), null);
        keyChanged.headers().add("__debezium.newkey", Map.of("car_code", "CAR-2"), null);
        SourceRecord cleaned = transform.apply(keyChanged);
        if (cleaned.headers().lastWithName("__debezium.oldkey") != null
                || cleaned.headers().lastWithName("__debezium.newkey") != null) {
            throw new AssertionError("schema-less Debezium key-change headers were preserved");
        }
        SourceRecord tombstone = new SourceRecord(
            Map.of(), Map.of(), TOPIC, 0, null, Map.of("car_code", "CAR-1"), null, null
        );
        if (transform.apply(tombstone) != null) {
            throw new AssertionError("tombstone behavior changed");
        }
        transform.close();
    }
}
