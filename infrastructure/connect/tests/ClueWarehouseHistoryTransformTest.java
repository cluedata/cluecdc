package io.cluecdc.connect;

import java.util.HashMap;
import java.util.Map;
import org.apache.kafka.common.record.TimestampType;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.sink.SinkRecord;

public final class ClueWarehouseHistoryTransformTest {
    private ClueWarehouseHistoryTransformTest() { }

    public static void main(String[] args) {
        var transform = new ClueWarehouseHistoryTransform<SinkRecord>();
        Map<String, Object> envelope = new HashMap<>();
        envelope.put("before", null);
        envelope.put("after", Map.of("id", 7, "status", "new"));
        envelope.put("source", Map.of("db", "shop"));
        envelope.put("op", "c");
        SinkRecord input = new SinkRecord(
            "orders", 2, null, Map.of("id", 7), null,
            envelope,
            42L, 1234L, TimestampType.CREATE_TIME
        );
        SinkRecord output = transform.apply(input);
        Struct row = (Struct) output.value();
        assert row.getString("_clue_topic").equals("orders");
        assert row.getInt32("_clue_partition") == 2;
        assert row.getInt64("_clue_offset") == 42L;
        assert row.getString("_clue_operation").equals("c");
        assert row.getString("_clue_after").contains("\"status\":\"new\"");

        SinkRecord tombstone = new SinkRecord("orders", 2, null, null, null, null, 43L);
        assert transform.apply(tombstone) == null;
    }
}
