# Oracle LogMiner source setup

**Experimental; live Oracle E2E NOT RUN. Not production-ready.** Use Oracle 19c or
21c. XStream/OpenLogReplicator, RAC, downstream mining and cloud-specific
topologies are not enabled by this implementation.

Port defaults to 1521. `database_name` identifies the CDB/database used by
Debezium. `provider_options.service_name` is the mining service. For multitenant
capture, connect mining diagnostics to CDB$ROOT and set `pdb_name`; optionally set
`pdb_service_name` for metadata discovery when the service differs from the PDB
name. A non-CDB may use `sid` instead of service/PDB settings. Mixed-case quoted
schema/table identifiers retain catalog spelling and use escaped quoted SQL.
Names outside the verified Kafka topic/filter character set are rejected at
capture validation rather than silently renamed.

TLS uses TCPS, hostname/DN matching and trusted certificates. API python-oracledb
uses thin mode; no Instant Client binary is added. Connect requires an Oracle
JDBC driver. The inspected existing upstream image contains ojdbc11 21.15.0.0;
this extension neither downloads nor adds a proprietary driver. Verify your
image, JDBC/database compatibility and redistribution/license terms before
deploying. A visible OracleConnector class alone is insufficient evidence.

DBA setup must follow the [upstream 3.3 Oracle guide](https://debezium.io/documentation/reference/3.3/connectors/oracle.html)
for the actual CDB/PDB and account topology. Examples below are deliberately
incomplete rather than a universal privileged-account provisioning script:

```sql
SELECT log_mode, supplemental_log_data_min FROM v$database;
-- DBA-reviewed maintenance operation, not a ClueCDC diagnostic:
ALTER DATABASE ADD SUPPLEMENTAL LOG DATA;
ALTER TABLE "APP"."ORDERS" ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;
-- Apply required container scope and privileges according to your topology:
GRANT CREATE SESSION, LOGMINING, SELECT ANY TRANSACTION TO C##CLUECDC;
GRANT EXECUTE ON SYS.DBMS_LOGMNR TO C##CLUECDC;
GRANT EXECUTE ON SYS.DBMS_LOGMNR_D TO C##CLUECDC;
```

ARCHIVELOG requires an approved administrative maintenance procedure. Additional
catalog/fixed-view, snapshot/table access and LogMiner staging privileges from
the upstream setup remain necessary, including applicable V_$DATABASE,
V_$LOG, V_$ARCHIVED_LOG, V_$LOGFILE, V_$LOGMNR_CONTENTS and transaction views.
Do not treat the examples as a least-privilege complete grant list. Readiness
reports missing access and never executes administrative changes.

Diagnostics check database version, root/PDB topology, ARCHIVELOG, supplemental
logging, LogMiner privileges/package access and redo/archive visibility. Catalog
archive age is not proof that files still exist or remain readable. Monitor
physical archive/FRA capacity and retention longer than the outage window.

Discovery includes schemas, columns, validated primary/unique constraints,
composite keys and nullability. Full-column supplemental logging is required for
selected tables. NUMBER with declared precision/scale (up to 38), character
types, DATE as a timestamp, TIMESTAMP/timezone, RAW and BINARY_FLOAT/DOUBLE have
explicit logical classifications. Unconstrained/negative-scale NUMBER, LOBs,
XML, JSON and local-timezone timestamps remain unverified and block capture.
They are never represented by placeholder values while claiming success.

Initial snapshot and streaming configs use LogMiner and stable schema history;
SCNs stay in Debezium offsets. Only raw S3/MinIO delivery is eligible, still
unverified against a live Oracle instance. Online table changes, incremental
resnapshot and stateful delivery are blocked. Missing archives/SCN history
require operator action and destination reconciliation; no automatic SCN reset,
connector recreation or resnapshot is implemented.

Before promotion, supply a licensed Oracle test instance and execute real
snapshot/CRUD/restart/outage and missing-log tests against destination records,
including quoted identifiers, PDB services, all required V_$ privileges and the
chosen JDBC driver. There is intentionally no automatically downloaded Oracle
Compose image or implicit EULA acceptance.
