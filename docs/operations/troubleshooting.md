# Troubleshooting

Start with service state and sanitized logs:

```bash
docker compose ps
docker compose logs --tail 100 cluecdc-api kafka-connect kafka
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

| Error or symptom           | Action                                                                                                     |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `SOURCE_AUTH_FAILED`       | Verify the database role/password and remember that changing `.env` does not rotate an initialized volume. |
| `SOURCE_UNAVAILABLE`       | Use a container DNS name from UI forms and check database health/network/TLS.                              |
| `DESTINATION_AUTH_FAILED`  | Verify the delivery role and target database.                                                              |
| `CONNECT_UNAVAILABLE`      | Check Connect health, URL, plugin startup, and Docker logs.                                                |
| `CONNECTOR_CONFIG_INVALID` | Review the redacted field diagnostics returned by validation.                                              |
| `KAFKA_UNAVAILABLE`        | Check broker listeners and use `kafka:29092` from containers.                                              |
| `SINK_PLUGIN_MISSING`      | Inspect Connect plugin discovery and rebuild the supplied Connect image.                                   |
| Pipeline `DEGRADED`        | Inspect connector tasks and the correlation/request ID in API logs.                                        |
| No recent changes          | Confirm topic offsets, create a source change, and inspect payloads directly with Kafka or at the destination. |
| API fails at startup       | Validate required settings and JSON formatting in `.env`.                                                  |

If a paused demo sink still receives rows, inspect
`http://localhost:8083/connectors` for an unmanaged/orphan sink left after an
earlier metadata reset. Review its configuration before explicitly deleting it;
ClueCDC never deletes unknown external connectors.

Do not paste an entire `.env`, connector configuration, payload, or unredacted
logs into an issue. Run `python scripts/verify-secrets.py` against a live demo
before sharing diagnostic artifacts.
