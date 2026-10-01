# Removing tables

Removing a table updates capture configuration and marks the pipeline-table association removed. It does not delete the Kafka topic, its retained records, or destination data. Remove or update dependent delivery mappings first when needed.

Review source publication membership and replication resources after permanent decommissioning. ClueCDC deliberately avoids destructive cleanup of external data-plane state.
