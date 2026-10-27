"""Live non-primary effective-key CDC acceptance test.

Creates disposable PostgreSQL and MySQL fixtures and verifies discovery, Kafka
keys, snapshot/streaming CRUD, identity changes, and post-restart delivery.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import uuid

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    Compose,
    load_fixture_environment,
    reconcile_records,
    wait_for,
)
from kafka_records import sample_records


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument("--compose-project", default="cluecdc")
    parser.add_argument("--connect-internal-url", default="http://kafka-connect:8083")
    parser.add_argument("--env-file", action="append", type=pathlib.Path)
    parser.add_argument(
        "--artifact",
        type=pathlib.Path,
        default=ROOT / "artifacts/cdc-effective-key.json",
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
    if api.request(f"/sources/{source_id}/cdc-readiness")["status"] == "failed":
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
    connector = status.get("connector", status)
    tasks = status.get("tasks", [])
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


def create_tables(compose: Compose, run: str) -> dict[str, dict[str, str]]:
    names = {
        provider: {
            "unique": f"qa_{run}_{provider[:2]}_customers_unique",
            "composite": f"qa_{run}_{provider[:2]}_orders_composite",
            "nullable": f"qa_{run}_{provider[:2]}_nullable_unique",
        }
        for provider in ("postgresql", "mysql")
    }
    pg = names["postgresql"]
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"""
        SET ROLE source_user;
        CREATE TABLE public.{pg["unique"]} (
          customer_code varchar(64) NOT NULL UNIQUE,
          full_name varchar(255), email varchar(255), status varchar(32)
        );
        CREATE TABLE public.{pg["composite"]} (
          tenant_id integer NOT NULL,
          external_order_id varchar(64) NOT NULL,
          customer_name varchar(255), amount numeric(18,2), status varchar(32),
          UNIQUE (tenant_id, external_order_id)
        );
        CREATE TABLE public.{pg["nullable"]} (
          external_ref varchar(64) UNIQUE, payload varchar(255)
        );
        ALTER TABLE public.{pg["unique"]} REPLICA IDENTITY FULL;
        ALTER TABLE public.{pg["composite"]} REPLICA IDENTITY FULL;
        INSERT INTO public.{pg["unique"]}
          VALUES ('PG-U1', 'Snapshot PostgreSQL', 'pg1@example.test', 'new');
        INSERT INTO public.{pg["composite"]}
          VALUES (1, 'PG-O1', 'Snapshot PostgreSQL', 10.10, 'new');
        INSERT INTO public.{pg["nullable"]} VALUES (NULL, 'nullable identity');
        """,
    )
    my = names["mysql"]
    compose.mysql(
        "mysql-demo",
        "cluecdc_source",
        f"""
        CREATE TABLE {my["unique"]} (
          customer_code varchar(64) NOT NULL UNIQUE,
          full_name varchar(255), email varchar(255), status varchar(32)
        );
        CREATE TABLE {my["composite"]} (
          tenant_id int NOT NULL,
          external_order_id varchar(64) NOT NULL,
          customer_name varchar(255), amount decimal(18,2), status varchar(32),
          UNIQUE (tenant_id, external_order_id)
        );
        CREATE TABLE {my["nullable"]} (
          external_ref varchar(64) UNIQUE, payload varchar(255)
        );
        INSERT INTO {my["unique"]}
          VALUES ('MY-U1', 'Snapshot MySQL', 'my1@example.test', 'new');
        INSERT INTO {my["composite"]}
          VALUES (1, 'MY-O1', 'Snapshot MySQL', 10.10, 'new');
        INSERT INTO {my["nullable"]} VALUES (NULL, 'nullable identity');
        """,
    )
    return names


def assert_discovery(
    api: ApiClient, source: dict, names: dict[str, str]
) -> dict[str, dict]:
    rows = api.request(f"/sources/{source['id']}/tables")
    found = {
        row["table_name"]: row for row in rows if row["table_name"] in names.values()
    }
    if set(found) != set(names.values()):
        raise AcceptanceError("Effective-key fixtures were not all discovered")
    expected = {
        names["unique"]: ["customer_code"],
        names["composite"]: ["tenant_id", "external_order_id"],
    }
    for table_name, columns in expected.items():
        row = found[table_name]
        assessment = row["sync_assessment"]
        if row["primary_key_columns"]:
            raise AcceptanceError(f"{table_name} unexpectedly has a primary key")
        if assessment["effective_key"]["mode"] != "UNIQUE_INDEX":
            raise AcceptanceError(f"{table_name} did not select its unique index")
        if assessment["effective_key"]["columns"] != columns:
            raise AcceptanceError(f"{table_name} effective-key order is incorrect")
        if assessment["capabilities"]["current_state"]["status"] != "SUPPORTED":
            raise AcceptanceError(f"{table_name} is not safe for CURRENT_STATE")
        if assessment["capabilities"]["incremental_snapshot"] != "UNSUPPORTED":
            raise AcceptanceError("Non-PK incremental snapshot was falsely advertised")
    nullable = found[names["nullable"]]["sync_assessment"]
    if nullable["effective_key"]["mode"] != "NONE":
        raise AcceptanceError("Nullable unique identity was accepted")
    return found


def assert_nullable_rejected(
    api: ApiClient,
    source: dict,
    kafka_id: str,
    connect_id: str,
    schema: str,
    table: str,
    run: str,
) -> None:
    try:
        api.create(
            "/pipelines",
            {
                "name": f"Rejected nullable {run} {source['type']}",
                "source_id": source["id"],
                "kafka_cluster_id": kafka_id,
                "connect_cluster_id": connect_id,
                "topic_prefix": f"rejected_{source['type']}_{run}",
                "snapshot_mode": "initial",
                "tables": [
                    {
                        "schema_name": schema,
                        "table_name": table,
                        "sync_mode": "CURRENT_STATE",
                    }
                ],
            },
        )
    except AcceptanceError as error:
        if "SYNC_MODE_UNSUPPORTED" not in str(error):
            raise
    else:
        raise AcceptanceError("Nullable unique CURRENT_STATE deployment was accepted")


def stream_changes(compose: Compose, provider: str, names: dict[str, str]) -> None:
    prefix = "PG" if provider == "postgresql" else "MY"
    schema_prefix = "public." if provider == "postgresql" else ""
    unique = schema_prefix + names["unique"]
    composite = schema_prefix + names["composite"]
    statements = f"""
        INSERT INTO {unique} VALUES
          ('{prefix}-U2', 'Streaming {prefix}', '{prefix.lower()}2@example.test', 'new');
        UPDATE {unique} SET status='active', full_name='Updated {prefix}'
          WHERE customer_code='{prefix}-U2';
        DELETE FROM {unique} WHERE customer_code='{prefix}-U1';
        UPDATE {unique} SET customer_code='{prefix}-U22'
          WHERE customer_code='{prefix}-U2';
        INSERT INTO {composite} VALUES
          (2, '{prefix}-O2', 'Streaming {prefix}', 20.20, 'new');
        UPDATE {composite} SET amount=30.30, status='active'
          WHERE tenant_id=2 AND external_order_id='{prefix}-O2';
        DELETE FROM {composite}
          WHERE tenant_id=1 AND external_order_id='{prefix}-O1';
        UPDATE {composite} SET tenant_id=3
          WHERE tenant_id=2 AND external_order_id='{prefix}-O2';
    """
    if provider == "postgresql":
        compose.postgres(
            "postgres-demo", "cluecdc_source", "SET ROLE source_user;" + statements
        )
    else:
        compose.mysql("mysql-demo", "cluecdc_source", statements)


def query_rows(
    compose: Compose,
    provider: str,
    source: bool,
    schema: str,
    table: str,
    key_type: str,
) -> list[dict[str, object]]:
    service = "postgres-demo" if provider == "postgresql" else "mysql-demo"
    database = "cluecdc_source" if source else "cluecdc_destination"
    qualified = f"{schema}.{table}" if provider == "postgresql" else table
    if key_type == "unique":
        columns = ["customer_code", "full_name", "email", "status"]
        order = "customer_code"
    else:
        columns = [
            "tenant_id",
            "external_order_id",
            "customer_name",
            "amount",
            "status",
        ]
        order = "tenant_id, external_order_id"
    if provider == "postgresql":
        selected = ",".join(
            "amount::text AS amount" if column == "amount" else column
            for column in columns
        )
        raw = compose.postgres(
            service,
            database,
            f"SELECT COALESCE(json_agg(row_to_json(r))::text, '[]') "
            f"FROM (SELECT {selected} FROM {qualified} ORDER BY {order}) r;",
        )
    else:
        pairs = ",".join(
            f"'{column}',CAST(amount AS CHAR)"
            if column == "amount"
            else f"'{column}',{column}"
            for column in columns
        )
        raw = compose.mysql(
            service,
            database,
            f"SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT({pairs})), JSON_ARRAY()) "
            f"FROM (SELECT * FROM {qualified} ORDER BY {order}) ordered_rows;",
        )
    return json_rows(raw, f"{provider} {database}.{table}")


def assert_kafka_keys(
    topic: str, columns: list[str], old_value: str, new_value: str
) -> dict:
    sampled = wait_for(
        lambda: sample_records(topic),
        lambda value: any(event.get("key") for event in value["events"]),
        f"Kafka keys for {topic}",
        120,
    )
    keys = [
        event["key"]
        for event in sampled["events"]
        if isinstance(event.get("key"), dict)
    ]
    if not keys or any(list(key) != columns for key in keys):
        raise AcceptanceError(f"Kafka key columns do not match {columns!r} for {topic}")
    encoded = json.dumps(keys)
    if old_value not in encoded or new_value not in encoded:
        raise AcceptanceError(f"Kafka key change was not represented for {topic}")
    return {"topic": topic, "columns": columns, "keys_observed": len(keys)}


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
    if missing := [key for key in required if not environment.get(key)]:
        raise AcceptanceError("Missing acceptance credentials: " + ", ".join(missing))
    api = ApiClient(args.api_url, [environment[key] for key in required])
    api.authenticate(environment)
    compose = Compose(args.compose_project, env_files)
    run = uuid.uuid4().hex[:8]
    names = create_tables(compose, run)
    kafka = api.create(
        "/kafka/clusters",
        {"name": f"Effective key Kafka {run}", "bootstrap_servers": "kafka:29092"},
    )
    connect = api.create(
        "/connect/clusters",
        {
            "name": f"Effective key Connect {run}",
            "base_url": args.connect_internal_url,
            "kafka_cluster_id": kafka["id"],
        },
    )
    sources = {
        "postgresql": api.create(
            "/sources",
            {
                "name": f"Effective key PostgreSQL {run}",
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
                "name": f"Effective key MySQL {run}",
                "type": "mysql",
                "host": "mysql-demo",
                "port": 3306,
                "database_name": "cluecdc_source",
                "username": "source_user",
                "password": environment["MYSQL_DEMO_SOURCE_PASSWORD"],
            },
        ),
    }
    discovery = {}
    for provider, source in sources.items():
        discover(api, source["id"])
        discovery[provider] = assert_discovery(api, source, names[provider])
        assert_nullable_rejected(
            api,
            source,
            kafka["id"],
            connect["id"],
            "public" if provider == "postgresql" else "cluecdc_source",
            names[provider]["nullable"],
            run,
        )

    pipelines = {}
    for provider, source in sources.items():
        pipelines[provider] = deploy_pipeline(
            api,
            name=f"Effective key {provider} {run}",
            source_id=source["id"],
            kafka_id=kafka["id"],
            connect_id=connect["id"],
            topic_prefix=f"effective_{provider}_{run}",
            schema="public" if provider == "postgresql" else "cluecdc_source",
            tables=[names[provider]["unique"], names[provider]["composite"]],
        )
    destinations = {
        "postgresql": api.create(
            "/destinations",
            {
                "name": f"Effective key PostgreSQL destination {run}",
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
                "name": f"Effective key MySQL destination {run}",
                "type": "mysql",
                "host": "mysql-demo",
                "port": 3306,
                "database_name": "cluecdc_destination",
                "username": "destination_user",
                "password": environment["MYSQL_DEMO_DESTINATION_PASSWORD"],
            },
        ),
    }
    deliveries = {}
    target_names = {}
    for source_provider, destination_provider in (
        ("postgresql", "mysql"),
        ("mysql", "postgresql"),
    ):
        targets = [
            f"ek_{source_provider[:2]}_{run}_unique",
            f"ek_{source_provider[:2]}_{run}_composite",
        ]
        target_names[source_provider] = targets
        deliveries[source_provider] = deploy_delivery(
            api,
            destination_id=destinations[destination_provider]["id"],
            pipeline=pipelines[source_provider],
            source_schema="public"
            if source_provider == "postgresql"
            else "cluecdc_source",
            source_tables=[
                names[source_provider]["unique"],
                names[source_provider]["composite"],
            ],
            destination_schema="public"
            if destination_provider == "postgresql"
            else "cluecdc_destination",
            destination_tables=targets,
            name=f"Effective {source_provider} to {destination_provider} {run}",
        )

    for provider in ("postgresql", "mysql"):
        stream_changes(compose, provider, names[provider])

    kafka_evidence = []
    for source_provider, destination_provider in (
        ("postgresql", "mysql"),
        ("mysql", "postgresql"),
    ):
        prefix = "PG" if source_provider == "postgresql" else "MY"
        source_schema = (
            "public" if source_provider == "postgresql" else "cluecdc_source"
        )
        destination_schema = (
            "public" if destination_provider == "postgresql" else "cluecdc_destination"
        )
        for index, (key_type, keys) in enumerate(
            (
                ("unique", ["customer_code"]),
                ("composite", ["tenant_id", "external_order_id"]),
            )
        ):
            source_table = names[source_provider][key_type]
            target_table = target_names[source_provider][index]

            def expected() -> list[dict[str, object]]:
                return query_rows(
                    compose,
                    source_provider,
                    True,
                    source_schema,
                    source_table,
                    key_type,
                )

            def actual() -> list[dict[str, object]]:
                return query_rows(
                    compose,
                    destination_provider,
                    False,
                    destination_schema,
                    target_table,
                    key_type,
                )

            wait_for(
                actual,
                lambda rows, source_rows=expected: rows == source_rows(),
                f"rows in {target_table}",
                240,
            )
            reconcile_records(
                expected(),
                actual(),
                key_fields=keys,
                decimal_fields=["amount"] if key_type == "composite" else [],
            )
            topic = f"{pipelines[source_provider]['topic_prefix']}.{source_schema}.{source_table}"
            kafka_evidence.append(
                assert_kafka_keys(
                    topic,
                    keys,
                    f"{prefix}-U2" if key_type == "unique" else "2",
                    f"{prefix}-U22" if key_type == "unique" else "3",
                )
            )

        api.request(f"/pipelines/{pipelines[source_provider]['id']}/restart", "POST")
        post_code = f"{prefix}-POST-RESTART"
        source_table = names[source_provider]["unique"]
        if source_provider == "postgresql":
            compose.postgres(
                "postgres-demo",
                "cluecdc_source",
                f"SET ROLE source_user; INSERT INTO public.{source_table} VALUES "
                f"('{post_code}', 'After restart', NULL, 'active');",
            )
        else:
            compose.mysql(
                "mysql-demo",
                "cluecdc_source",
                f"INSERT INTO {source_table} VALUES "
                f"('{post_code}', 'After restart', NULL, 'active');",
            )
        wait_for(
            lambda: query_rows(
                compose,
                destination_provider,
                False,
                destination_schema,
                target_names[source_provider][0],
                "unique",
            ),
            lambda rows, code=post_code: any(
                row["customer_code"] == code for row in rows
            ),
            f"post-restart row {post_code}",
            240,
        )

    details = {
        provider: api.request(f"/pipelines/{pipeline['id']}")
        for provider, pipeline in pipelines.items()
    }
    for provider, detail in details.items():
        key_config = detail["config"].get("message.key.columns", "")
        for table in (names[provider]["unique"], names[provider]["composite"]):
            if table not in key_config:
                raise AcceptanceError(
                    "Deployed Debezium configuration omitted an effective key"
                )

    artifact = {
        "verified": True,
        "run_id": run,
        "pipelines": {key: value["id"] for key, value in pipelines.items()},
        "deliveries": {key: value["id"] for key, value in deliveries.items()},
        "discovery": {
            provider: {
                key: discovery[provider][table]["sync_assessment"]["effective_key"]
                for key, table in names[provider].items()
            }
            for provider in discovery
        },
        "kafka": kafka_evidence,
        "checks": [
            "snapshot",
            "insert",
            "non_key_update",
            "delete",
            "key_update_old_row_removed",
            "composite_key_order",
            "nullable_unique_rejected",
            "direct_kafka_keys",
            "post_connector_restart",
        ],
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print("Effective row-key CDC acceptance PASSED", flush=True)


if __name__ == "__main__":
    main()
