# API v1

Base: `/api/v1`. OpenAPI: API `/docs` and `/openapi.json`. Frontend requests use the web server's `/api/v1` proxy; every request has X-Correlation-ID. Errors are `{error:{code,message,details}}`. Validation details contain field locations/types rather than original inputs. Unexpected errors return a safe message; logs contain correlation ID, method/path/status/duration.

| Resource                 | Endpoints                                                                                                            |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| Sources                  | GET/POST /sources; GET/PUT/DELETE /sources/{uuid}                                                                    |
| Source operations        | POST /sources/{uuid}/test, /discover, /health; GET /tables, /cdc-readiness                                           |
| Jobs                     | GET /jobs/{uuid}                                                                                                     |
| Kafka clusters           | GET/POST /kafka/clusters; PUT/DELETE /kafka/clusters/{uuid}                                                          |
| Connect clusters         | GET/POST /connect/clusters; PUT/DELETE /connect/clusters/{uuid}                                                      |
| Plugin discovery         | GET /connect/clusters/{uuid}/plugins                                                                                 |
| Destinations             | GET/POST /destinations; POST /destinations/test-connection; GET/PUT/DELETE /destinations/{uuid}                      |
| Connections              | GET/POST /connections; POST /connections/test; GET/PUT/DELETE /connections/{uuid}; POST /connections/{uuid}/test     |
| Lakehouse targets        | GET/POST /lakehouse-targets; GET/PUT/DELETE /lakehouse-targets/{uuid}                                                |
| Iceberg delivery         | POST /lakehouse-targets/{uuid}/preview-delivery, /deploy                                                            |
| Deliveries               | GET /deliveries; GET/DELETE /deliveries/{uuid}; GET /deliveries/{uuid}/status                                        |
| Delivery lifecycle       | POST /deliveries/{uuid}/pause, /resume, /restart, /restart-task?task=0; PUT /deliveries/{uuid}/mappings              |
| Destination delivery     | POST /destinations/{uuid}/test, /preview, /deploy; GET /mappings, /status                                            |
| Destination lifecycle    | POST /destinations/{uuid}/pause, /resume, /restart; optional delivery_id query                                       |
| Delivery mapping/removal | PUT /destinations/{uuid}/deliveries/{delivery_uuid}/mappings; DELETE /destinations/{uuid}/deliveries/{delivery_uuid} |
| Delivery task restart    | POST /destinations/{uuid}/deliveries/{delivery_uuid}/tasks/{task_id}/restart                                         |
| Pipeline destinations    | GET /pipelines/{uuid}/destinations; also included in pipeline detail                                                 |
| Pipelines                | GET/POST /pipelines; POST /pipelines/preview; GET/DELETE /pipelines/{uuid}                                           |
| Lifecycle                | GET /pipelines/{uuid}/status; POST /deploy, /pause, /resume, /restart, /restart-task?task=0                          |
| Connectors               | GET /connect/connectors; GET /connect/connectors/{name}/status?cluster_id=uuid                                       |
| Topics                   | GET /kafka/topics?cluster_id=uuid; GET /kafka/topics/{name}?cluster_id=uuid                                          |
| Events                   | GET /events?cluster_id=uuid&topic=prefix.schema.table                                                                |
| Operations               | GET /monitoring/overview, /operations/errors, /audit                                                                 |
| Incident lifecycle       | PATCH /operations/errors/{uuid}?status=OPEN\|ACKNOWLEDGED\|RESOLVED                                                  |
| Schema versions          | GET /data/schemas?source_id=uuid                                                                                     |
| Session                  | GET /session                                                                                                         |

Connection credentials are write-only. Responses expose a configured flag and
mask, never secret values. Lakehouse targets use Amazon S3 or MinIO storage and
the Iceberg connector's built-in Hadoop catalog.

Discovery/health return 202 with a durable Job. Poll until COMPLETED/FAILED; results and sanitized errors are stored. Test/readiness requests are bounded asynchronous read-only database calls. Source tables support search/schema/cdc_ready/has_primary_key/min_size filters. Discovery/list bounds are 500 entries; audit supports a bounded limit and resource_id filter.

Pipeline creation validates with Connect but does not deploy; deployment is a separate explicit operation. Preview generates config without saving metadata and without exposing a secret value. A deployment error leaves the validated draft available for retry. API metadata mutations and operations are authorized/audited. Desired state is stored separately from observed runtime state.

Destination registration stores an encrypted logical target without claiming delivery. Unsaved connection testing does not persist it. Delivery preview validates actual topics, discovered schema/keys, target readiness, plugin installation and Connect configuration. Deploy creates a sink connector before persisting its delivery association, with compensation if metadata persistence fails. Mapping updates repeat validation. Destination status observes each independent sink; connection health is separately tested. Delivery removal retains target data. See [destination behavior and limits](../connectors/postgresql-destination.md).

Audit supports `meaningful=true` to exclude routine state observations/jobs from overview activity and `include_related=true` with `resource_id` to include a destination's delivery actions. Operational errors include nullable destination and connector associations. Incident updates require operations permission and are audited. The same-origin web proxy supports PATCH as well as GET/POST/PUT/DELETE.

Event filters: limit 1–200; partition >=0; offset >=0; operation CREATE/UPDATE/DELETE/READ; table; primary-key substring `key`; epoch-millisecond start_ms/end_ms. Offset/time/key filters never extend the recent bounded scan window. Unknown/unavailable metrics are JSON null. Connector traces are withheld; task identity/state remains inspectable.

The machine-only `/internal/secrets/{uuid}` is outside API v1, excluded from OpenAPI, and requires its own service credential. Ordinary Admin/Viewer tokens cannot use it. It is intentionally never routed by the web proxy.
