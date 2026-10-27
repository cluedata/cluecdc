"""Live CDC integrity matrix for PostgreSQL/MySQL sources and JDBC sinks.

The script creates run-scoped source tables, two Debezium pipelines, and four
JDBC deliveries. It verifies snapshot plus streaming materialization without
deleting offsets, connectors, or pre-existing data.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import uuid
from collections.abc import Callable

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    Compose,
    load_fixture_environment,
    reconcile_records,
    wait_for,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument("--compose-project", default="cluecdc")
    parser.add_argument(
        "--kafka-config-env", help="Environment variable holding Kafka fixture JSON"
    )
    parser.add_argument("--connect-internal-url", default="http://kafka-connect:8083")
    parser.add_argument(
        "--artifact", type=pathlib.Path, default=ROOT / "artifacts/cdc-integrity.json"
    )
    parser.add_argument(
        "--env-file",
        action="append",
        type=pathlib.Path,
        help="Dotenv file used by the isolated Compose stack; may be repeated",
    )
    return parser.parse_args()


def json_rows(raw: str, label: str) -> list[dict[str, object]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AcceptanceError(f"{label} did not return valid JSON") from error
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise AcceptanceError(f"{label} did not return a JSON record array")
    return value


def discover(api: ApiClient, source_id: str) -> None:
    api.request(f"/sources/{source_id}/test", "POST")
    readiness = api.request(f"/sources/{source_id}/cdc-readiness")
    if readiness["status"] == "failed":
        raise AcceptanceError("Source CDC readiness failed")
    job = api.request(f"/sources/{source_id}/discover", "POST")
    completed = wait_for(
        lambda: api.request(f"/jobs/{job['id']}"),
        lambda item: item["status"] in {"COMPLETED", "FAILED"},
        "source discovery",
    )
    if completed["status"] != "COMPLETED":
        raise AcceptanceError("Source discovery failed")


def running(api: ApiClient, path: str) -> bool:
    status = api.request(path)
    tasks = status.get("tasks", [])
    connector = status.get("connector", status)
    return (
        bool(tasks)
        and connector.get("state", connector.get("actual_state")) == "RUNNING"
        and all(task.get("state") == "RUNNING" for task in tasks)
    )


def deploy_pipeline(
    api: ApiClient,
    *,
    name: str,
    source_id: str,
    kafka_id: str,
    connect_id: str,
    topic_prefix: str,
    schema: str,
    tables: list[str],
) -> dict:
    pipeline = api.create(
        "/pipelines",
        {
            "name": name,
            "source_id": source_id,
            "kafka_cluster_id": kafka_id,
            "connect_cluster_id": connect_id,
            "topic_prefix": topic_prefix,
            "snapshot_mode": "initial",
            "tables": [
                {"schema_name": schema, "table_name": table} for table in tables
            ],
        },
    )
    api.request(f"/pipelines/{pipeline['id']}/deploy", "POST")
    wait_for(
        lambda: running(api, f"/pipelines/{pipeline['id']}/status"),
        bool,
        f"pipeline {name}",
        240,
    )
    return pipeline


def deploy_delivery(
    api: ApiClient,
    *,
    destination_id: str,
    pipeline: dict,
    source_schema: str,
    source_tables: list[str],
    destination_schema: str,
    destination_tables: list[str],
    name: str,
) -> dict:
    payload = {
        "name": name,
        "pipeline_id": pipeline["id"],
        "mappings": [
            {
                "topic": f"{pipeline['topic_prefix']}.{source_schema}.{source_table}",
                "schema_name": destination_schema,
                "table_name": destination_table,
            }
            for source_table, destination_table in zip(
                source_tables, destination_tables, strict=True
            )
        ],
    }
    preview = api.create(f"/destinations/{destination_id}/preview", payload)
    if preview["config"].get("connection.password") != "[REDACTED]":
        raise AcceptanceError("JDBC preview did not redact the destination password")
    delivery = api.create(f"/destinations/{destination_id}/deploy", payload)
    wait_for(
        lambda: running(api, f"/deliveries/{delivery['id']}/status"),
        bool,
        f"delivery {name}",
        240,
    )
    return delivery


def create_source_fixtures(compose: Compose, run: str) -> dict[str, list[str]]:
    tables = [f"cdc_i_{run}_accounts", f"cdc_i_{run}_ledger"]
    accounts, ledger = tables
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"""
        SET ROLE source_user;
        CREATE TABLE public.{accounts} (
          id bigint PRIMARY KEY,
          name text NOT NULL,
          note text NULL,
          balance numeric(30,8) NOT NULL,
          occurred_at timestamptz NOT NULL
        );
        CREATE TABLE public.{ledger} (
          id bigint PRIMARY KEY,
          account_id bigint NOT NULL REFERENCES public.{accounts}(id),
          amount numeric(30,8) NOT NULL,
          note text NULL,
          occurred_at timestamptz NOT NULL
        );
        ALTER TABLE public.{accounts} REPLICA IDENTITY FULL;
        ALTER TABLE public.{ledger} REPLICA IDENTITY FULL;
        INSERT INTO public.{accounts}
          VALUES (1, 'Ảnh chụp {run} 👋', NULL, 123456789012345.12345678,
                  '2026-10-14 12:30:00+07');
        INSERT INTO public.{ledger}
          VALUES (10, 1, 0.00000001, 'khởi tạo', '2026-10-14 12:31:00+07');
        """,
    )
    compose.mysql(
        "mysql-demo",
        "cluecdc_source",
        f"""
        CREATE TABLE {accounts} (
          id BIGINT NOT NULL PRIMARY KEY,
          name VARCHAR(255) NOT NULL,
          note TEXT NULL,
          balance DECIMAL(30,8) NOT NULL,
          occurred_at DATETIME(6) NOT NULL
        );
        CREATE TABLE {ledger} (
          id BIGINT NOT NULL PRIMARY KEY,
          account_id BIGINT NOT NULL,
          amount DECIMAL(30,8) NOT NULL,
          note TEXT NULL,
          occurred_at DATETIME(6) NOT NULL,
          CONSTRAINT fk_{run} FOREIGN KEY (account_id) REFERENCES {accounts}(id)
        );
        INSERT INTO {accounts}
          VALUES (1, 'Ảnh chụp {run} 👋', NULL, 123456789012345.12345678,
                  '2026-10-14 05:30:00.000000');
        INSERT INTO {ledger}
          VALUES (10, 1, 0.00000001, 'khởi tạo', '2026-10-14 05:31:00.000000');
        """,
    )
    return {"postgresql": tables, "mysql": tables}


def stream_changes(
    compose: Compose, provider: str, tables: list[str], run: str
) -> None:
    accounts, ledger = tables
    if provider == "postgresql":
        compose.postgres(
            "postgres-demo",
            "cluecdc_source",
            f"""
            SET ROLE source_user;
            BEGIN;
            INSERT INTO public.{accounts}
              VALUES (2, 'Giao dịch {run} 🌏', NULL, 10.10000000,
                      '2026-10-14 20:00:00+07');
            INSERT INTO public.{ledger}
              VALUES (20, 2, 10.10000000, NULL, '2026-10-14 20:00:01+07');
            COMMIT;
            UPDATE public.{accounts} SET balance=20.20000000 WHERE id=2;
            UPDATE public.{accounts}
              SET name='Cập nhật lần hai {run} ✓', balance=30.30000000 WHERE id=2;
            INSERT INTO public.{accounts}
              VALUES (3, 'sẽ xóa', NULL, 1.00000000, '2026-10-14 13:00:00+00');
            DELETE FROM public.{accounts} WHERE id=3;
            INSERT INTO public.{accounts}
              VALUES (4, 'đổi khóa', NULL, 44.00000000, '2026-10-14 13:01:00+00');
            UPDATE public.{accounts} SET id=44 WHERE id=4;
            """,
        )
    else:
        compose.mysql(
            "mysql-demo",
            "cluecdc_source",
            f"""
            START TRANSACTION;
            INSERT INTO {accounts}
              VALUES (2, 'Giao dịch {run} 🌏', NULL, 10.10000000,
                      '2026-10-14 13:00:00.000000');
            INSERT INTO {ledger}
              VALUES (20, 2, 10.10000000, NULL, '2026-10-14 13:00:01.000000');
            COMMIT;
            UPDATE {accounts} SET balance=20.20000000 WHERE id=2;
            UPDATE {accounts}
              SET name='Cập nhật lần hai {run} ✓', balance=30.30000000 WHERE id=2;
            INSERT INTO {accounts}
              VALUES (3, 'sẽ xóa', NULL, 1.00000000, '2026-10-14 13:00:00.000000');
            DELETE FROM {accounts} WHERE id=3;
            INSERT INTO {accounts}
              VALUES (4, 'đổi khóa', NULL, 44.00000000, '2026-10-14 13:01:00.000000');
            UPDATE {accounts} SET id=44 WHERE id=4;
            """,
        )


def query_rows(
    compose: Compose,
    provider: str,
    service: str,
    database: str,
    schema: str,
    table: str,
    amount_field: str,
) -> list[dict[str, object]]:
    identity_field = "name" if amount_field == "balance" else "account_id"
    columns = (
        f"id,{identity_field},note,{amount_field}::text AS {amount_field},"
        "to_char(occurred_at AT TIME ZONE 'UTC', "
        '\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') AS occurred_at'
    )
    if provider == "postgresql":
        raw = compose.postgres(
            service,
            database,
            f"SELECT COALESCE(json_agg(row_to_json(r))::text, '[]') "
            f"FROM (SELECT {columns} FROM {schema}.{table} ORDER BY id) r;",
        )
    else:
        raw = compose.mysql(
            service,
            database,
            f"SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT("
            f"'id',id,'{identity_field}',{identity_field},'note',note,"
            f"'{amount_field}',CAST({amount_field} AS CHAR),"
            "'occurred_at',DATE_FORMAT(occurred_at,'%Y-%m-%dT%H:%i:%s.%f'))), JSON_ARRAY()) "
            f"FROM (SELECT * FROM {table} ORDER BY id) ordered_rows;",
        )
    return json_rows(raw, f"{provider} {table}")


def reconcile_pair(
    source_rows: Callable[[str, str], list[dict[str, object]]],
    destination_rows: Callable[[str, str], list[dict[str, object]]],
    source_tables: list[str],
    destination_tables: list[str],
) -> None:
    for source_table, destination_table, amount_field in zip(
        source_tables,
        destination_tables,
        ["balance", "amount"],
        strict=True,
    ):
        expected_ids = {1, 2, 44} if amount_field == "balance" else {10, 20}
        wait_for(
            lambda table=destination_table, field=amount_field: destination_rows(
                table, field
            ),
            lambda rows, ids=expected_ids: {row["id"] for row in rows} == ids,
            f"materialized rows in {destination_table}",
            240,
        )
        reconcile_records(
            source_rows(source_table, amount_field),
            destination_rows(destination_table, amount_field),
            key_fields=["id"],
            decimal_fields=[amount_field],
            timestamp_fields=["occurred_at"],
        )


def main() -> None:
    args = arguments()
    env_files = args.env_file or [ROOT / ".env"]
    environment = load_fixture_environment(env_files)
    required = [
        "POSTGRES_DEMO_SOURCE_PASSWORD",
        "MYSQL_DEMO_SOURCE_PASSWORD",
        "POSTGRES_DEMO_DESTINATION_PASSWORD",
        "MYSQL_DEMO_DESTINATION_PASSWORD",
    ]
    missing = [key for key in required if not environment.get(key)]
    if missing:
        raise AcceptanceError("Missing acceptance credentials: " + ", ".join(missing))
    kafka_config = (
        json.loads(os.environ[args.kafka_config_env]) if args.kafka_config_env else {}
    )
    api = ApiClient(
        args.api_url,
        [environment[key] for key in required]
        + [
            value
            for key, value in kafka_config.items()
            if key in {"password", "ssl_ca_pem", "ssl_certificate_pem", "ssl_key_pem"}
        ],
    )
    api.authenticate(environment)
    compose = Compose(args.compose_project, env_files)
    run = uuid.uuid4().hex[:8]
    source_tables = create_source_fixtures(compose, run)

    kafka = api.create(
        "/kafka/clusters",
        {
            "name": f"Integrity Kafka {run}",
            "bootstrap_servers": "kafka:29092",
            **kafka_config,
        },
    )
    connect = api.create(
        "/connect/clusters",
        {
            "name": f"Integrity Connect {run}",
            "base_url": args.connect_internal_url,
            "kafka_cluster_id": kafka["id"],
        },
    )
    sources = {
        "postgresql": api.create(
            "/sources",
            {
                "name": f"Integrity PostgreSQL {run}",
                "type": "postgresql",
                "host": "postgres-demo",
                "port": 5432,
                "database_name": "cluecdc_source",
                "username": "source_user",
                "password": environment["POSTGRES_DEMO_SOURCE_PASSWORD"],
            },
        ),
        "mysql": api.create(
            "/sources",
            {
                "name": f"Integrity MySQL {run}",
                "type": "mysql",
                "host": "mysql-demo",
                "port": 3306,
                "database_name": "cluecdc_source",
                "username": "source_user",
                "password": environment["MYSQL_DEMO_SOURCE_PASSWORD"],
            },
        ),
    }
    for source in sources.values():
        discover(api, source["id"])

    pipelines = {
        provider: deploy_pipeline(
            api,
            name=f"Integrity {provider} {run}",
            source_id=sources[provider]["id"],
            kafka_id=kafka["id"],
            connect_id=connect["id"],
            topic_prefix=f"integrity_{provider}_{run}",
            schema="public" if provider == "postgresql" else "cluecdc_source",
            tables=source_tables[provider],
        )
        for provider in ("postgresql", "mysql")
    }
    destinations = {
        "postgresql": api.create(
            "/destinations",
            {
                "name": f"Integrity PostgreSQL destination {run}",
                "type": "postgresql",
                "host": "postgres-demo",
                "port": 5432,
                "database_name": "cluecdc_destination",
                "username": "destination_user",
                "password": environment["POSTGRES_DEMO_DESTINATION_PASSWORD"],
            },
        ),
        "mysql": api.create(
            "/destinations",
            {
                "name": f"Integrity MySQL destination {run}",
                "type": "mysql",
                "host": "mysql-demo",
                "port": 3306,
                "database_name": "cluecdc_destination",
                "username": "destination_user",
                "password": environment["MYSQL_DEMO_DESTINATION_PASSWORD"],
            },
        ),
    }
    for destination in destinations.values():
        api.request(f"/destinations/{destination['id']}/test", "POST")

    destination_tables: dict[tuple[str, str], list[str]] = {}
    deliveries = []
    for source_provider in ("postgresql", "mysql"):
        for destination_provider in ("postgresql", "mysql"):
            target_tables = [
                f"from_{source_provider[:2]}_{run}_accounts",
                f"from_{source_provider[:2]}_{run}_ledger",
            ]
            destination_tables[(source_provider, destination_provider)] = target_tables
            deliveries.append(
                deploy_delivery(
                    api,
                    destination_id=destinations[destination_provider]["id"],
                    pipeline=pipelines[source_provider],
                    source_schema="public"
                    if source_provider == "postgresql"
                    else "cluecdc_source",
                    source_tables=source_tables[source_provider],
                    destination_schema=(
                        "public"
                        if destination_provider == "postgresql"
                        else "cluecdc_destination"
                    ),
                    destination_tables=target_tables,
                    name=f"{source_provider} to {destination_provider} {run}",
                )
            )

    for provider in ("postgresql", "mysql"):
        stream_changes(compose, provider, source_tables[provider], run)

    for source_provider in ("postgresql", "mysql"):
        source_schema = (
            "public" if source_provider == "postgresql" else "cluecdc_source"
        )
        source_service = (
            "postgres-demo" if source_provider == "postgresql" else "mysql-demo"
        )
        source_database = "cluecdc_source"

        def source_query(
            table: str,
            amount: str,
            provider: str = source_provider,
            service: str = source_service,
            database: str = source_database,
            schema: str = source_schema,
        ) -> list[dict[str, object]]:
            return query_rows(
                compose,
                provider,
                service,
                database,
                schema,
                table,
                amount,
            )

        for destination_provider in ("postgresql", "mysql"):
            destination_service = (
                "postgres-demo"
                if destination_provider == "postgresql"
                else "mysql-demo"
            )
            destination_database = "cluecdc_destination"
            destination_schema = (
                "public"
                if destination_provider == "postgresql"
                else "cluecdc_destination"
            )

            def destination_query(
                table: str,
                amount: str,
                provider: str = destination_provider,
                service: str = destination_service,
                database: str = destination_database,
                schema: str = destination_schema,
            ) -> list[dict[str, object]]:
                return query_rows(
                    compose, provider, service, database, schema, table, amount
                )

            reconcile_pair(
                source_query,
                destination_query,
                source_tables[source_provider],
                destination_tables[(source_provider, destination_provider)],
            )
            print(
                f"PASS {source_provider} -> {destination_provider}: "
                "snapshot, transaction, CRUD, Unicode, NULL, decimal, timestamp, PK update",
                flush=True,
            )

    artifact = {
        "run_id": run,
        "matrix": [
            f"{source}->{destination}"
            for source in ("postgresql", "mysql")
            for destination in ("postgresql", "mysql")
        ],
        "pipeline_ids": {key: value["id"] for key, value in pipelines.items()},
        "delivery_ids": [delivery["id"] for delivery in deliveries],
        "semantics": "JDBC materialized state with at-least-once replay and idempotent upsert",
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print("CDC integrity matrix PASSED", flush=True)


if __name__ == "__main__":
    main()
