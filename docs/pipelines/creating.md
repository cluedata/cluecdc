# Creating a pipeline

Before creation, an Admin registers and tests a source, destination, Kafka
cluster, and Kafka Connect cluster, then runs source discovery. Admin or Ops can
use the wizard to select tables, snapshot behavior, topic prefix, advanced
connector options, an existing destination, and topic-to-target mappings.

Validation checks readiness, selected-table keys, topic names, connector plugin
configuration, destination access, mappings, and identity conflicts. The UI
wizard previews and saves capture, deploys its Debezium connector, prepares
topics, validates delivery, and deploys the first sink connector. It reports a
partial result and supports retry when capture succeeds but delivery fails.

API clients can call preview, create, capture deployment, topic preparation,
delivery preview, and delivery deployment separately. Watch actual connector
and task state after the final API call returns.
