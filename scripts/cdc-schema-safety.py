"""Live snapshot, table lifecycle, and schema-change safety acceptance test."""

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
    wait_for,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument("--compose-project", default="cluecdc")
    parser.add_argument(
        "--artifact", type=pathlib.Path, default=ROOT / "artifacts/cdc-integrity.json"
    )
    parser.add_argument("--env-file", action="append", type=pathlib.Path)
    return parser.parse_args()


def discover(api: ApiClient, source_id: str) -> None:
    job = api.request(f"/sources/{source_id}/discover", "POST")
    result = wait_for(
        lambda: api.request(f"/jobs/{job['id']}"),
        lambda item: item["status"] in {"COMPLETED", "FAILED"},
        "source discovery",
        180,
    )
    if result["status"] != "COMPLETED":
        raise AcceptanceError("Source discovery failed")


def wait_operation(
    api: ApiClient, operation_id: str, label: str, seconds: int = 300
) -> dict:
    result = wait_for(
        lambda: api.request(f"/operations/{operation_id}"),
        lambda item: item["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"},
        label,
        seconds,
    )
    if result["status"] != "SUCCEEDED":
        raise AcceptanceError(
            f"{label} ended as {result['status']}: "
            f"{result.get('error_code') or 'no safe error code'}"
        )
    return result


def wait_pipeline(api: ApiClient, pipeline_id: str, label: str) -> None:
    def healthy() -> bool:
        status = api.request(f"/pipelines/{pipeline_id}/status")
        return (
            status.get("connector", {}).get("state") == "RUNNING"
            and bool(status.get("tasks"))
            and all(task.get("state") == "RUNNING" for task in status["tasks"])
        )

    wait_for(healthy, bool, label, 240)


def wait_count(
    compose: Compose,
    table: str,
    where: str,
    expected: str,
    label: str,
    seconds: int = 240,
) -> None:
    wait_for(
        lambda: compose.postgres(
            "postgres-demo",
            "cluecdc_destination",
            f"SELECT count(*) FROM public.{table} WHERE {where};",
        ),
        lambda value: value == expected,
        label,
        seconds,
    )


def main() -> None:
    args = arguments()
    env_files = args.env_file or [ROOT / ".env"]
    environment = load_fixture_environment(env_files)
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    prior_pipeline = artifact["pipeline_ids"]["postgresql"]
    api = ApiClient(
        args.api_url,
        [
            environment.get("POSTGRES_DEMO_SOURCE_PASSWORD", ""),
            environment.get("POSTGRES_DEMO_DESTINATION_PASSWORD", ""),
        ],
    )
    compose = Compose(args.compose_project, env_files)
    prior = api.request(f"/pipelines/{prior_pipeline}")
    prior_deliveries = api.request("/deliveries")
    postgres_delivery = next(
        item
        for item in prior_deliveries
        if item["id"] in artifact["delivery_ids"]
        and item["pipeline_id"] == prior_pipeline
        and item["destination"]["provider"] == "POSTGRESQL"
    )
    source_id = prior["source_id"]
    destination_id = postgres_delivery["destination_id"]
    run = uuid.uuid4().hex[:8]
    source_base = f"cdc_s_{run}_base"
    source_added = f"cdc_s_{run}_added"
    source_bulk = f"cdc_s_{run}_bulk"
    source_probe = f"cdc_s_{run}_probe"
    source_unsupported = f"cdc_s_{run}_unsupported"
    destination_base = f"schema_{run}_base"
    destination_added = f"schema_{run}_added"
    destination_bulk = f"schema_{run}_bulk"
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"""
        SET ROLE source_user;
        CREATE TABLE public.{source_base} (
          id bigint PRIMARY KEY, name varchar(32) NOT NULL, balance numeric(12,2) NOT NULL
        );
        CREATE TABLE public.{source_added} (
          id bigint PRIMARY KEY, name text NOT NULL
        );
        CREATE TABLE public.{source_bulk} (
          id bigint PRIMARY KEY, name text NOT NULL
        );
        CREATE TABLE public.{source_probe} (
          id bigint PRIMARY KEY, code varchar(32) NOT NULL
        );
        CREATE TABLE public.{source_unsupported} (
          id bigint PRIMARY KEY, measures integer[] NOT NULL
        );
        ALTER TABLE public.{source_base} REPLICA IDENTITY FULL;
        ALTER TABLE public.{source_added} REPLICA IDENTITY FULL;
        ALTER TABLE public.{source_bulk} REPLICA IDENTITY FULL;
        INSERT INTO public.{source_base} VALUES (1, 'initial', 10.25);
        INSERT INTO public.{source_added} VALUES (1, 'backfill');
        INSERT INTO public.{source_bulk}
          SELECT value, 'bulk-' || value FROM generate_series(1, 5000) value;
        """,
    )
    discover(api, source_id)
    pipeline = api.create(
        "/pipelines",
        {
            "name": f"Schema safety {run}",
            "source_id": source_id,
            "kafka_cluster_id": prior["kafka_cluster_id"],
            "connect_cluster_id": prior["connect_cluster_id"],
            "topic_prefix": f"schema_safety_{run}",
            "snapshot_mode": "initial",
            "tables": [{"schema_name": "public", "table_name": source_base}],
            "additional_debezium_properties": {"incremental.snapshot.chunk.size": "10"},
        },
    )
    api.request(f"/pipelines/{pipeline['id']}/deploy", "POST")
    wait_pipeline(api, pipeline["id"], "schema test pipeline")
    base_mapping = {
        "topic": f"{pipeline['topic_prefix']}.public.{source_base}",
        "schema_name": "public",
        "table_name": destination_base,
    }
    delivery = api.create(
        f"/destinations/{destination_id}/deploy",
        {
            "name": f"Schema safety delivery {run}",
            "pipeline_id": pipeline["id"],
            "mappings": [base_mapping],
        },
    )
    wait_count(
        compose, destination_base, "id=1 AND name='initial'", "1", "initial snapshot"
    )
    print("PASS initial snapshot followed by streaming-ready delivery", flush=True)

    add_operation = api.create(
        f"/pipelines/{pipeline['id']}/tables",
        {
            "tables": [
                {
                    "schema_name": "public",
                    "table_name": source_added,
                    "initial_data_strategy": "BACKFILL",
                    "destination_handling": "AUTO_CREATE",
                    "destination_schema": "public",
                    "destination_table": destination_added,
                }
            ]
        },
    )[0]
    wait_operation(api, add_operation["id"], "add-table backfill")
    wait_count(
        compose,
        destination_added,
        "id=1 AND name='backfill'",
        "1",
        "added snapshot row",
    )
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"SET ROLE source_user; INSERT INTO public.{source_added} VALUES (2, 'streaming');",
    )
    wait_count(
        compose, destination_added, "id=2 AND name='streaming'", "1", "added stream row"
    )
    wait_count(
        compose, destination_base, "id=1 AND name='initial'", "1", "unrelated base row"
    )
    print("PASS add-table backfill, stream, and unrelated-table isolation", flush=True)

    tables = api.request(f"/pipelines/{pipeline['id']}/tables")
    added_table = next(item for item in tables if item["table_name"] == source_added)
    compose.postgres(
        "postgres-demo",
        "cluecdc_destination",
        f"UPDATE public.{destination_added} SET name='corrupt' WHERE id=1;",
    )
    resync = api.create(
        f"/pipeline-tables/{added_table['id']}/resync", {"scope": "ENTIRE_TABLE"}
    )
    wait_operation(api, resync["id"], "incremental resnapshot")
    wait_count(
        compose,
        destination_added,
        "id=1 AND name='backfill'",
        "1",
        "resnapshot repair",
    )
    print("PASS incremental resnapshot repaired materialized state", flush=True)

    bulk_operation = api.create(
        f"/pipelines/{pipeline['id']}/tables",
        {
            "tables": [
                {
                    "schema_name": "public",
                    "table_name": source_bulk,
                    "initial_data_strategy": "BACKFILL",
                    "destination_handling": "AUTO_CREATE",
                    "destination_schema": "public",
                    "destination_table": destination_bulk,
                }
            ]
        },
    )[0]
    wait_for(
        lambda: api.request(f"/operations/{bulk_operation['id']}"),
        lambda item: (
            item["status"] == "WAITING" and item["current_step"] == "wait_snapshot"
        ),
        "bulk snapshot start",
        240,
    )
    compose.restart("kafka-connect")
    wait_pipeline(api, pipeline["id"], "connector restart during snapshot")
    wait_operation(api, bulk_operation["id"], "snapshot after connector restart", 420)
    wait_count(compose, destination_bulk, "TRUE", "5000", "bulk snapshot rows", 300)
    print("PASS connector restart during snapshot without corruption", flush=True)

    bulk_table = next(
        item
        for item in api.request(f"/pipelines/{pipeline['id']}/tables")
        if item["table_name"] == source_bulk
    )
    cancel = api.create(
        f"/pipeline-tables/{bulk_table['id']}/resync", {"scope": "ENTIRE_TABLE"}
    )
    wait_for(
        lambda: api.request(f"/operations/{cancel['id']}"),
        lambda item: (
            item["status"] == "WAITING" and item["current_step"] == "wait_snapshot"
        ),
        "cancellable snapshot start",
        240,
    )
    try:
        api.request(
            f"/pipelines/{pipeline['id']}/tables/{bulk_table['id']}",
            "DELETE",
            {"destination_handling": "KEEP_DATA"},
        )
    except AcceptanceError as error:
        if "TABLE_OPERATION_ACTIVE" not in str(error):
            raise
    else:
        raise AcceptanceError(
            "Concurrent remove was accepted during an active snapshot"
        )
    stopped = api.request(f"/pipeline-tables/{bulk_table['id']}/snapshot/stop", "POST")
    if stopped["status"] != "CANCELLED":
        raise AcceptanceError("Snapshot stop did not persist a cancelled operation")
    print("PASS snapshot cancellation and concurrent-operation guard", flush=True)

    remove = api.request(
        f"/pipelines/{pipeline['id']}/tables/{added_table['id']}",
        "DELETE",
        {"destination_handling": "KEEP_DATA"},
    )
    wait_operation(api, remove["id"], "remove table")
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"""
        SET ROLE source_user;
        BEGIN;
        INSERT INTO public.{source_base} VALUES (2, 'base-alive', 20.50);
        INSERT INTO public.{source_added} VALUES (3, 'must-not-capture');
        COMMIT;
        """,
    )
    wait_count(
        compose,
        destination_base,
        "id=2 AND name='base-alive'",
        "1",
        "base after remove",
    )
    wait_count(compose, destination_added, "id=3", "0", "removed table isolation")
    print(
        "PASS table removal preserved unrelated capture and destination data",
        flush=True,
    )

    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"ALTER TABLE public.{source_base} ADD COLUMN note text NULL, "
        "ADD COLUMN status text NOT NULL DEFAULT 'new';",
    )
    discover(api, source_id)
    current_delivery = next(
        item for item in api.request("/deliveries") if item["id"] == delivery["id"]
    )
    mappings_payload = {
        **current_delivery["configuration_json"],
        "pipeline_id": pipeline["id"],
        "name": current_delivery["name"],
        "mappings": current_delivery["topic_mapping_json"],
    }
    api.request(
        f"/destinations/{destination_id}/deliveries/{delivery['id']}/mappings",
        "PUT",
        mappings_payload,
    )
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"SET ROLE source_user; INSERT INTO public.{source_base}(id,name,balance,note) "
        "VALUES (3, 'schema-add', 30.75, NULL);",
    )
    wait_count(
        compose,
        destination_base,
        "id=3 AND note IS NULL AND status='new'",
        "1",
        "nullable/default column evolution",
    )
    base_table = next(
        item
        for item in api.request(f"/pipelines/{pipeline['id']}/tables")
        if item["table_name"] == source_base
    )
    evolution_resync = api.create(
        f"/pipeline-tables/{base_table['id']}/resync", {"scope": "ENTIRE_TABLE"}
    )
    wait_operation(api, evolution_resync["id"], "schema evolution resnapshot")
    wait_count(
        compose,
        destination_base,
        "id IN (1,2,3) AND status='new'",
        "3",
        "defaulted existing rows after resnapshot",
    )
    print(
        "PASS nullable/defaulted column evolution and explicit resnapshot",
        flush=True,
    )

    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"ALTER TABLE public.{source_probe} ADD COLUMN note text NULL, "
        "ADD COLUMN status text NOT NULL DEFAULT 'new';",
    )
    discover(api, source_id)
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"ALTER TABLE public.{source_probe} ALTER COLUMN code TYPE varchar(64);",
    )
    discover(api, source_id)
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"ALTER TABLE public.{source_probe} DROP COLUMN note;",
    )
    discover(api, source_id)
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"ALTER TABLE public.{source_probe} ALTER COLUMN code TYPE bigint USING code::bigint;",
    )
    discover(api, source_id)
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"ALTER TABLE public.{source_probe} DROP CONSTRAINT {source_probe}_pkey, "
        "ADD PRIMARY KEY (code);",
    )
    discover(api, source_id)
    versions = [
        item
        for item in api.request(
            f"/data/schemas?source_id={source_id}&changes_only=true"
        )
        if item["table_name"] == source_probe
    ]
    classifications = {
        (change["change"], change.get("column"), change["classification"])
        for version in versions
        for change in version["diff_json"]
    }
    required = {
        ("column_added", "note", "NON_BREAKING"),
        ("column_added", "status", "NON_BREAKING"),
        ("type_changed", "code", "NON_BREAKING"),
        ("column_removed", "note", "BREAKING"),
        ("type_changed", "code", "BREAKING"),
        ("primary_key_changed", None, "BREAKING"),
    }
    if not required <= classifications:
        raise AcceptanceError(
            "Schema compatibility history did not classify expected changes"
        )

    unsupported = api.create(
        "/pipelines",
        {
            "name": f"Unsupported schema {run}",
            "source_id": source_id,
            "kafka_cluster_id": prior["kafka_cluster_id"],
            "connect_cluster_id": prior["connect_cluster_id"],
            "topic_prefix": f"unsupported_schema_{run}",
            "snapshot_mode": "no_data",
            "tables": [{"schema_name": "public", "table_name": source_unsupported}],
        },
    )
    api.request(f"/pipelines/{unsupported['id']}/deploy", "POST")
    wait_pipeline(api, unsupported["id"], "unsupported-type probe pipeline")
    try:
        api.create(
            f"/destinations/{destination_id}/preview",
            {
                "pipeline_id": unsupported["id"],
                "mappings": [
                    {
                        "topic": f"{unsupported['topic_prefix']}.public.{source_unsupported}",
                        "schema_name": "public",
                        "table_name": f"unsupported_{run}",
                    }
                ],
            },
        )
    except AcceptanceError as error:
        if "DELIVERY_TYPE_UNSUPPORTED" not in str(error):
            raise
    else:
        raise AcceptanceError("Unsupported array schema was accepted for JDBC delivery")
    print(
        "PASS schema compatibility matrix and actionable unsupported-type rejection",
        flush=True,
    )

    report = {
        "verified": True,
        "run_id": run,
        "pipeline_id": pipeline["id"],
        "delivery_id": delivery["id"],
        "checks": [
            "initial_snapshot",
            "add_table_backfill",
            "incremental_resnapshot",
            "restart_during_snapshot",
            "cancel_snapshot",
            "concurrent_operation_guard",
            "remove_table_isolation",
            "nullable_and_default_column_evolution",
            "schema_compatibility_matrix",
            "unsupported_type_rejection",
        ],
    }
    (ROOT / "artifacts" / "cdc-schema-safety.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print("CDC snapshot and schema safety PASSED", flush=True)


if __name__ == "__main__":
    main()
