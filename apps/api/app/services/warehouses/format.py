import csv
import io
import json

HISTORY_COLUMNS = [
    "_clue_topic",
    "_clue_partition",
    "_clue_offset",
    "_clue_timestamp",
    "_clue_operation",
    "_clue_key",
    "_clue_before",
    "_clue_after",
    "_clue_source",
    "_clue_batch_id",
]


def serialize_history_csv(events: list[dict], batch_id: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    rows = sorted(
        events,
        key=lambda item: (str(item["topic"]), int(item["partition"]), int(item["offset"])),
    )
    for event in rows:
        row = {
            "_clue_topic": event["topic"],
            "_clue_partition": int(event["partition"]),
            "_clue_offset": int(event["offset"]),
            "_clue_timestamp": event.get("timestamp"),
            "_clue_operation": event.get("operation"),
            "_clue_key": event.get("key"),
            "_clue_before": event.get("before"),
            "_clue_after": event.get("after"),
            "_clue_source": event.get("source"),
        }
        writer.writerow(
            [
                row["_clue_topic"],
                row["_clue_partition"],
                row["_clue_offset"],
                row["_clue_timestamp"],
                row["_clue_operation"],
                json.dumps(row["_clue_key"], separators=(",", ":"))
                if row["_clue_key"] is not None
                else None,
                json.dumps(row["_clue_before"], separators=(",", ":"))
                if row["_clue_before"] is not None
                else None,
                json.dumps(row["_clue_after"], separators=(",", ":"))
                if row["_clue_after"] is not None
                else None,
                json.dumps(row["_clue_source"], separators=(",", ":"))
                if row["_clue_source"] is not None
                else None,
                batch_id,
            ]
        )
    return output.getvalue().encode("utf-8")
