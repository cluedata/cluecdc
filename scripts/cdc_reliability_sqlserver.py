"""SQL Server API E2E using the runtime's real Linux driver for fixture SQL."""

from decimal import Decimal

from cdc_reliability import AMOUNT, TEXT, Scenario, amount
from cdc_reliability_support import api_for, wait_value


def run_sqlserver(stack, evidence):
    stack.add_source("sqlserver")
    scenario = Scenario(stack, evidence, "sqlserver", "MINIO")
    scenario.schema = "dbo"
    scenario.topic = scenario.table + ".inventory.dbo." + scenario.table
    api = api_for(stack)
    scenario.api = api

    def sql(statement, database="inventory"):
        script = """import sys
import mssql_python
db=mssql_python.connect("Server=tcp:sqlserver-source,1433;Database="+sys.argv[1]+";UID=sa;PWD={Local-CDC-Fixture-Password-123!};Encrypt=no;TrustServerCertificate=no;",autocommit=True,timeout=10)
try:
 cursor=db.cursor()
 cursor.execute(sys.stdin.read())
 cursor.close()
finally: db.close()
"""
        return stack.exec(
            "cluecdc-api", ["python", "-c", script, database], statement, timeout=30
        )

    def available(database="inventory"):
        try:
            sql("SELECT 1", database)
            return True
        except Exception:
            return False

    def setup():
        wait_value(
            lambda: available("master"),
            bool,
            "SQL Server runtime driver readiness",
            timeout=120,
        )
        sql("IF DB_ID('inventory') IS NULL CREATE DATABASE inventory", "master")
        sql(
            "IF (SELECT is_cdc_enabled FROM sys.databases WHERE name=DB_NAME())=0 EXEC sys.sp_cdc_enable_db"
        )
        sql(
            f"CREATE TABLE dbo.[{scenario.table}](id INT PRIMARY KEY,label NVARCHAR(255),amount DECIMAL(30,9)); INSERT INTO dbo.[{scenario.table}] VALUES(1,N'{TEXT}',{AMOUNT})"
        )
        sql(
            f"EXEC sys.sp_cdc_enable_table @source_schema=N'dbo',@source_name=N'{scenario.table}',@role_name=NULL"
        )
        source = api.create(
            "/sources",
            {
                "name": scenario.table,
                "type": "sqlserver",
                "host": "sqlserver-source",
                "port": 1433,
                "database_name": "inventory",
                "username": "sa",
                "password": "Local-CDC-Fixture-Password-123!",
                "ssl_enabled": False,
            },
        )
        scenario.source_connection = source
        readiness = api.create(f"/sources/{source['id']}/test", {})
        assert readiness["status"] != "failed"
        job = api.create(f"/sources/{source['id']}/discover", {})
        completed = wait_value(
            lambda: api.request(f"/jobs/{job['id']}"),
            lambda j: j["status"] in {"FAILED", "COMPLETED"},
            "SQL Server discovery",
        )
        assert completed["status"] == "COMPLETED"
        kafka = api.create(
            "/kafka/clusters",
            {"name": scenario.table, "bootstrap_servers": "kafka:29092"},
        )
        connect = api.create(
            "/connect/clusters",
            {
                "name": scenario.table,
                "base_url": "http://kafka-connect:8083",
                "kafka_cluster_id": kafka["id"],
            },
        )
        payload = {
            "name": scenario.table,
            "source_id": source["id"],
            "kafka_cluster_id": kafka["id"],
            "connect_cluster_id": connect["id"],
            "topic_prefix": scenario.table,
            "tables": [{"schema_name": "dbo", "table_name": scenario.table}],
        }
        api.create("/pipelines/preview", payload)
        scenario.pipeline = api.create("/pipelines", payload)
        api.create(f"/pipelines/{scenario.pipeline['id']}/deploy", {})
        scenario.wait_capture("RUNNING")
        scenario.deploy_destination()
        return {
            "readiness": readiness,
            "driver": "real mssql-python inside owned API runtime",
            "workflow": "existing API orchestration",
        }

    scenario.restore_probe = available

    def snapshot():
        events = wait_value(
            scenario.events,
            lambda rows: any(
                (r["value"] or {}).get("op") == "r"
                and (r["value"] or {}).get("source", {}).get("snapshot") == "last"
                for r in rows
            ),
            "SQL Server last snapshot event",
        )
        row = next(r for r in events if (r["value"] or {}).get("op") == "r")
        assert row["key"] == {"id": 1}
        assert row["value"]["after"]["label"] == TEXT
        assert amount(row["value"]["after"]["amount"]) == Decimal(AMOUNT)
        scenario.delivered(TEXT, 1)
        return {
            "snapshot_event": row,
            "completion": "Debezium last snapshot marker",
            "scope": "integer key, Unicode, decimal(30,9)",
        }

    def mutate(identity, label):
        scenario.expected.append({"operation": "c", "id": identity, "label": label})
        wait_value(available, bool, "SQL Server restored listener", timeout=120)
        sql(
            f"INSERT INTO dbo.[{scenario.table}] VALUES({identity},N'{label}',2.000000001)"
        )

    scenario.mutate = mutate

    def crud():
        mutate(2, "insert")
        scenario.delivered("insert", 2)
        scenario.expected.append(
            {"operation": "u", "id": 2, "label": "updated", "amount": "9.123456789"}
        )
        sql(
            f"UPDATE dbo.[{scenario.table}] SET label=N'updated',amount=9.123456789 WHERE id=2"
        )
        scenario.delivered("updated", 2)
        scenario.expected.append({"operation": "d", "id": 2})
        sql(f"DELETE FROM dbo.[{scenario.table}] WHERE id=2")
        rows = wait_value(
            scenario.rows,
            lambda rows: {"r", "c", "u", "d"} <= {r.get("op") for r in rows},
            "SQL Server destination CRUD history",
        )
        delete = next(r for r in rows if r["op"] == "d")
        assert delete["before"]["id"] == 2 and delete["after"] is None
        assert any(r["op"] == "u" and r["after"]["label"] == "updated" for r in rows)
        return {
            "records": len(rows),
            "expected_operations": scenario.expected,
            "delete": "raw history preserved; no stateful delete claim",
        }

    try:
        scenario.record(
            "connection", "API CRUD, readiness, discovery, preview/deploy", setup
        )
        scenario.record("snapshot", "Real snapshot and precision in MinIO", snapshot)
        scenario.record("crud", "Real SQL Server insert/update/delete history", crud)
        scenario.record(
            "recovery",
            "Existing API pause/resume/restart and audit",
            scenario.lifecycle,
        )
        for cycle in range(2):
            scenario.record(
                "outage-recovery-" + str(cycle),
                "Two owned source outages preserve capture/delivery and positions",
                lambda cycle=cycle: scenario.recover_fault(
                    "sqlserver-source", 10 + cycle * 2
                ),
            )
    finally:
        scenario.connect.close()
