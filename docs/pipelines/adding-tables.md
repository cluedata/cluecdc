# Adding tables

Run source discovery so the table and its current schema exist in ClueCDC. Open the pipeline **Tables** tab, choose **Add table**, review readiness, and submit the operation.

The worker updates the filtered publication where applicable, updates the connector's exact table list, and requests an incremental snapshot through Debezium signaling. The operation record exposes current step, progress when available, and a safe error code/message.
