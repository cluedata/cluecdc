# Error handling

API errors use `{"error":{"code","message","details"}}`. Validation responses include safe field locations and error types but omit supplied secret values. Unexpected failures return a correlation ID; server logs hold structured context without request bodies or authorization headers.

Operational incidents are durable records that can be acknowledged and resolved. Acknowledging documents investigation; it does not repair the connector. Runtime recovery can resolve matching active incidents, while notification delivery failures remain separate from pipeline lifecycle success.
