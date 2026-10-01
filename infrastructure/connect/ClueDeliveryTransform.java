package io.cluecdc.connect;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.apache.kafka.common.config.ConfigDef;
import org.apache.kafka.connect.connector.ConnectRecord;
import org.apache.kafka.connect.data.Decimal;
import org.apache.kafka.connect.data.Field;
import org.apache.kafka.connect.data.Schema;
import org.apache.kafka.connect.data.SchemaBuilder;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.errors.DataException;
import org.apache.kafka.connect.transforms.Transformation;

/** Restores explicitly discovered logical types for the JDBC sink only.
 * Never infers a database schema from row contents or emits row contents in errors.
 */
public final class ClueDeliveryTransform<R extends ConnectRecord<R>> implements Transformation<R> {
    private static final Pattern NUMERIC = Pattern.compile("(?:numeric|decimal)\\((\\d+),(\\d+)\\)");
    private static final ConfigDef CONFIG = new ConfigDef().define(
        "metadata", ConfigDef.Type.STRING, ConfigDef.Importance.HIGH, "Discovered schemas and target mappings");
    private final Map<String, Table> tables = new HashMap<>();
    private record Table(Schema key, Schema before, Schema after, Schema envelope,
                         String schema, String table) { }

    @Override public void configure(Map<String, ?> configs) {
        try {
            Map<String, Map<String, Object>> metadata = new ObjectMapper().readValue(
                (String) configs.get("metadata"), new TypeReference<>() { });
            if (metadata.isEmpty() || metadata.size() > 100) throw new IllegalArgumentException();
            for (var entry : metadata.entrySet()) {
                String name = entry.getKey().replaceAll("[^a-zA-Z0-9_]", "_");
                Map<String, Object> description = entry.getValue();
                List<?> columns = (List<?>) description.get("columns");
                List<?> keys = (List<?>) description.get("primary_keys");
                SchemaBuilder after = SchemaBuilder.struct().name(name + ".Value").optional();
                SchemaBuilder before = SchemaBuilder.struct().name(name + ".Before").optional();
                SchemaBuilder key = SchemaBuilder.struct().name(name + ".Key");
                for (Object item : columns) {
                    Map<?, ?> column = (Map<?, ?>) item;
                    String field = (String) column.get("name");
                    String logical = (String) column.get("logical_type");
                    String type = "DECIMAL".equals(logical)
                        ? (String) column.get("source_type")
                        : logical != null ? logical : (String) column.get("type");
                    after.field(field, fieldSchema(type, truthy(column.get("nullable"))));
                    before.field(field, fieldSchema(type, true));
                    if (keys.contains(field)) key.field(field, fieldSchema(type, false));
                }
                Schema source = SchemaBuilder.struct().name(name + ".Source")
                    .field("schema", Schema.STRING_SCHEMA).field("table", Schema.STRING_SCHEMA).build();
                Schema beforeSchema = before.build(), afterSchema = after.build();
                Schema envelope = SchemaBuilder.struct().name(name + ".Envelope")
                    .field("before", beforeSchema).field("after", afterSchema)
                    .field("source", source).field("op", Schema.STRING_SCHEMA)
                    .field("ts_ms", Schema.OPTIONAL_INT64_SCHEMA).build();
                tables.put(entry.getKey(), new Table(key.build(), beforeSchema, afterSchema, envelope,
                    (String) description.get("schema_name"), (String) description.get("table_name")));
            }
        } catch (Exception failure) {
            throw new DataException("Invalid ClueCDC delivery metadata; rediscover the source and update mappings");
        }
    }

    private static boolean truthy(Object value) {
        if (value instanceof Boolean booleanValue) return booleanValue;
        if (value instanceof Number numberValue) return numberValue.intValue() != 0;
        return value != null && Boolean.parseBoolean(value.toString());
    }

    private static Schema fieldSchema(String type, boolean optional) {
        SchemaBuilder builder;
        if (type.equals("INT8") || type.equals("INT16")) builder = SchemaBuilder.int16();
        else if (type.equals("INT24") || type.equals("INT32")) builder = SchemaBuilder.int32();
        else if (type.equals("UINT8") || type.equals("UINT16") || type.equals("UINT24") || type.equals("UINT32") || type.equals("INT64")) builder = SchemaBuilder.int64();
        else if (type.equals("UINT64")) builder = Decimal.builder(0).parameter("connect.decimal.precision", "20");
        else if (type.equals("FLOAT32")) builder = SchemaBuilder.float32();
        else if (type.equals("FLOAT64")) builder = SchemaBuilder.float64();
        else if (type.equals("BOOLEAN")) builder = SchemaBuilder.bool();
        else if (type.equals("BYTES") || type.equals("BIT")) builder = SchemaBuilder.bytes();
        else if (type.equals("DATE")) builder = SchemaBuilder.int32().name("io.debezium.time.Date");
        else if (type.equals("TIME")) builder = SchemaBuilder.int64().name("io.debezium.time.MicroTime");
        else if (type.equals("DATETIME") || type.equals("TIMESTAMP")) builder = SchemaBuilder.int64().name("io.debezium.time.MicroTimestamp");
        else if (type.equals("TIMESTAMPTZ")) builder = SchemaBuilder.string().name("io.debezium.time.ZonedTimestamp");
        else if (type.equals("DECIMAL")) builder = Decimal.builder(0);
        else if (List.of("STRING", "TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "JSON", "ENUM", "SET", "UUID", "YEAR").contains(type)) builder = SchemaBuilder.string();
        else {
        Matcher decimal = NUMERIC.matcher(type);
        if (decimal.matches()) {
            builder = Decimal.builder(Integer.parseInt(decimal.group(2)))
                .parameter("connect.decimal.precision", decimal.group(1));
        } else if (type.equals("smallint")) builder = SchemaBuilder.int16();
        else if (type.equals("integer")) builder = SchemaBuilder.int32();
        else if (type.equals("bigint")) builder = SchemaBuilder.int64();
        else if (type.equals("real")) builder = SchemaBuilder.float32();
        else if (type.equals("double precision")) builder = SchemaBuilder.float64();
        else if (type.equals("boolean")) builder = SchemaBuilder.bool();
        else if (type.equals("bytea")) builder = SchemaBuilder.bytes();
        else if (type.equals("date")) builder = SchemaBuilder.int32().name("io.debezium.time.Date");
        else if (type.startsWith("timestamp") && type.endsWith("without time zone")) {
            Matcher precision = Pattern.compile("timestamp\\((\\d+)\\).*").matcher(type);
            boolean milliseconds = precision.matches() && Integer.parseInt(precision.group(1)) <= 3;
            builder = SchemaBuilder.int64().name(milliseconds ? "io.debezium.time.Timestamp" : "io.debezium.time.MicroTimestamp");
        } else if (type.startsWith("timestamp") && type.endsWith("with time zone")) {
            builder = SchemaBuilder.string().name("io.debezium.time.ZonedTimestamp");
        } else if (type.equals("uuid")) builder = SchemaBuilder.string().name("io.debezium.data.Uuid");
        else if (type.equals("json") || type.equals("jsonb")) builder = SchemaBuilder.string().name("io.debezium.data.Json");
        else if (type.equals("text") || type.startsWith("character")) builder = SchemaBuilder.string();
        else throw new DataException("Unsupported database delivery type");
        }
        if (optional) builder.optional();
        return builder.build();
    }

    private static Object convert(Schema schema, Object value) {
        if (value == null) return null;
        if (Decimal.LOGICAL_NAME.equals(schema.name())) {
            int scale = Integer.parseInt(schema.parameters().get("scale"));
            if (value instanceof String text) return new BigDecimal(new BigInteger(Base64.getDecoder().decode(text)), scale);
            return new BigDecimal(value.toString()).setScale(scale);
        }
        return switch (schema.type()) {
            case INT16 -> ((Number) value).shortValue();
            case INT32 -> ((Number) value).intValue();
            case INT64 -> ((Number) value).longValue();
            case FLOAT32 -> ((Number) value).floatValue();
            case FLOAT64 -> ((Number) value).doubleValue();
            case BYTES -> Base64.getDecoder().decode((String) value);
            case STRING -> value.toString();
            case BOOLEAN -> value instanceof Boolean booleanValue
                ? booleanValue
                : value instanceof Number numberValue
                    ? numberValue.intValue() != 0
                    : Boolean.parseBoolean(value.toString());
            default -> throw new DataException("Unsupported ClueCDC record field");
        };
    }

    private static Map<?, ?> payload(Object value) {
        if (!(value instanceof Map<?, ?> map)) throw new DataException("ClueCDC delivery expects JSON change records");
        if (map.containsKey("schema") && map.containsKey("payload")) return (Map<?, ?>) map.get("payload");
        return map;
    }

    private static Struct row(Schema schema, Object value) {
        if (value == null) return null;
        Map<?, ?> data = payload(value);
        for (Object field : data.keySet()) {
            if (schema.field(field.toString()) == null) {
                throw new DataException("Source schema changed; rediscover the source and update delivery mappings");
            }
        }
        Struct result = new Struct(schema);
        for (Field field : schema.fields()) {
            Object fieldValue = data.get(field.name());
            if ("__debezium_unavailable_value".equals(fieldValue)) throw new DataException("Unavailable source column; delivery cannot safely overwrite it");
            result.put(field, convert(field.schema(), fieldValue));
        }
        return result;
    }

    @Override public R apply(R record) {
        if (record.value() == null) return null; // DELETE envelopes handle deletes; skip tombstones.
        Table table = tables.get(record.topic());
        if (table == null) throw new DataException("Topic has no ClueCDC delivery mapping");
        try {
            Map<?, ?> value = payload(record.value());
            String operation = (String) value.get("op");
            if (!List.of("c", "u", "d", "r").contains(operation)) throw new IllegalArgumentException();
            Schema sourceSchema = table.envelope().field("source").schema();
            Struct source = new Struct(sourceSchema).put("schema", table.schema()).put("table", table.table());
            Struct envelope = new Struct(table.envelope())
                .put("before", row(table.before(), value.get("before")))
                .put("after", row(table.after(), value.get("after")))
                .put("source", source).put("op", operation)
                .put("ts_ms", convert(Schema.OPTIONAL_INT64_SCHEMA, value.get("ts_ms")));
            return record.newRecord(record.topic(), record.kafkaPartition(), table.key(),
                row(table.key(), record.key()), table.envelope(), envelope, record.timestamp());
        } catch (Exception failure) {
            // Keep record contents and original exception text out of Connect task traces.
            throw new DataException(
                "ClueCDC change record does not match discovered metadata ("
                    + failure.getClass().getSimpleName() + ": " + failure.getMessage() + ")"
            );
        }
    }

    @Override public ConfigDef config() { return CONFIG; }
    @Override public void close() { tables.clear(); }
}
