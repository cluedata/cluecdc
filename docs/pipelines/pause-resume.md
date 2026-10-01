# Pause and resume

Pause and resume operate on the actual Kafka Connect connector and update desired state. A paused capture stops producing new records while source logs continue to accumulate. A paused delivery allows capture to continue and Kafka backlog to grow.

Confirm task state after each operation. `UNKNOWN` means the latest observation is unavailable; it is not success. Ensure source-log and Kafka retention can cover the pause duration.
