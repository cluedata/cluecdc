# Creating a pipeline

Before creation, register and test a source, Kafka cluster, and Kafka Connect cluster, then run source discovery. The wizard selects tables, snapshot behavior, topic prefix, and advanced connector options.

Validation checks readiness, selected-table keys, topic names, connector plugin configuration, and identity conflicts. Creation stores desired metadata; deployment prepares topics/publication state and creates the real connector. Watch actual connector and task state after the API call returns.
