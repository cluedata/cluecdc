# Adding a database connector

ClueCDC treats a database integration as a provider, not conditionals scattered
through routes or UI.

1. Add provider metadata and capability flags under `apps/api/app/providers`.
2. Implement source/destination adapters only for capabilities that work.
3. Add a typed Debezium source builder or JDBC destination URL/config behavior.
4. Register the provider in `providers/registry.py`.
5. Keep credentials in `SecretProvider`; generated Connect config must contain a
   secret reference, never a plaintext password.
6. Map connection, validation, and runtime failures to stable domain error codes.
7. Add unit tests for config/types and integration tests for connection,
   deployment, lifecycle, and deletion behavior.
8. Document database prerequisites, privileges, CDC settings, limitations, and
   destructive cleanup.
9. Expose UI selection from provider metadata. Do not duplicate Debezium config
   construction or database rules in React.

Do not mark a capability supported until the real path is tested. Planned
providers may have design documentation but must remain disabled in production UI.
