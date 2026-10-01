"""Generate committed ecommerce DML and explicit test-column DDL for ClueCDC."""

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

PREFIX = "cdc-load-"
TABLES = ("customers", "orders", "payments")
TYPES = {
    "text": "text",
    "bigint": "bigint",
    "numeric": "numeric(12,2)",
    "timestamptz": "timestamptz",
    "boolean": "boolean",
}


def emit(**values):
    print(json.dumps(values, default=str), flush=True)


def bounded_count(value):
    count = int(value)
    if not 1 <= count <= 10000:
        raise argparse.ArgumentTypeError("count must be between 1 and 10000")
    return count


def positive_seconds(value):
    seconds = float(value)
    if not 0 < seconds <= 86400:
        raise argparse.ArgumentTypeError(
            "seconds must be greater than 0 and at most 86400"
        )
    return seconds


def run_id(value):
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", value):
        raise argparse.ArgumentTypeError(
            "run ID must contain 1-64 letters, digits or hyphens"
        )
    return value


def test_column(value):
    if not re.fullmatch(r"cdc_test_[a-z0-9_]{1,54}", value):
        raise argparse.ArgumentTypeError(
            "column must start with cdc_test_ and use lowercase letters, digits or "
            "underscores (max 63 characters)"
        )
    return value


def table(name):
    return sql.Identifier(os.environ.get("PGSCHEMA", "public"), name)


def connect():
    # libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD; never log a DSN.
    connection = psycopg.connect(
        connect_timeout=5,
        application_name="cluecdc-commerce-generator",
        options="-c statement_timeout=15000 -c lock_timeout=5000",
        row_factory=dict_row,
    )
    return connection


def email_pattern(marker=None):
    return f"{PREFIX}{marker}.%@example.test" if marker else f"{PREFIX}%@example.test"


def insert_batch(connection, count, marker):
    first = None
    with connection.transaction():
        for _ in range(count):
            token = uuid.uuid4().hex
            total = Decimal(1000 + int(token[:4], 16) % 99000) / 100
            customer = connection.execute(
                sql.SQL(
                    "INSERT INTO {} (name,email) VALUES (%s,%s) RETURNING id"
                ).format(table("customers")),
                (f"CDC shopper {marker}", f"{PREFIX}{marker}.{token}@example.test"),
            ).fetchone()
            order = connection.execute(
                sql.SQL(
                    "INSERT INTO {} (customer_id,total,status) VALUES (%s,%s,%s) RETURNING id"
                ).format(table("orders")),
                (customer["id"], total, "pending"),
            ).fetchone()
            payment = connection.execute(
                sql.SQL(
                    "INSERT INTO {} (order_id,amount,status) VALUES (%s,%s,%s) RETURNING id"
                ).format(table("payments")),
                (order["id"], total, "pending"),
            ).fetchone()
            if first is None:
                first = {
                    "customer_id": customer["id"],
                    "order_id": order["id"],
                    "payment_id": payment["id"],
                }
    return {
        "operation": "insert",
        "run_id": marker,
        "customers": count,
        "orders": count,
        "payments": count,
        "first_ids": first,
    }


def update_batch(connection, count, marker=None):
    with connection.transaction():
        rows = connection.execute(
            sql.SQL(
                "SELECT id FROM {} WHERE email LIKE %s ORDER BY updated_at,id LIMIT %s "
                "FOR UPDATE SKIP LOCKED"
            ).format(table("customers")),
            (email_pattern(marker), count),
        ).fetchall()
        ids = [row["id"] for row in rows]
        customers = connection.execute(
            sql.SQL(
                "UPDATE {} SET name = CASE WHEN name LIKE %s THEN %s ELSE %s END, "
                "updated_at=clock_timestamp() WHERE id = ANY(%s)"
            ).format(table("customers")),
            (
                "CDC updated shopper%",
                "CDC shopper revisited",
                "CDC updated shopper",
                ids,
            ),
        ).rowcount
        orders = connection.execute(
            sql.SQL(
                "UPDATE {} SET status=CASE WHEN status='paid' THEN 'shipped' ELSE "
                "'paid' END, total=total+0.01, updated_at=clock_timestamp() WHERE "
                "customer_id=ANY(%s) RETURNING id,total"
            ).format(table("orders")),
            (ids,),
        ).fetchall()
        payments = 0
        for order in orders:
            payments += connection.execute(
                sql.SQL(
                    "UPDATE {} SET status=CASE WHEN status='completed' THEN 'settled' ELSE "
                    "'completed' END, amount=%s WHERE order_id=%s"
                ).format(table("payments")),
                (order["total"], order["id"]),
            ).rowcount
    return {
        "operation": "update",
        "run_id": marker or "all-generated",
        "customers": customers,
        "orders": len(orders),
        "payments": payments,
    }


def alter_schema(connection, args):
    with connection.transaction():
        # Serialize test DDL and inspect existence inside the same transaction.
        connection.execute(
            sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(table(args.table))
        )
        exists = (
            connection.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND "
                "table_name=%s AND column_name=%s",
                (os.environ.get("PGSCHEMA", "public"), args.table, args.column),
            ).fetchone()
            is not None
        )
        default = (
            sql.SQL("")
            if args.default is None
            else sql.SQL(" DEFAULT {}::{}").format(
                sql.Literal(args.default), sql.SQL(TYPES[args.type])
            )
        )
        if args.action == "add":
            if exists:
                return {
                    "operation": "alter",
                    "action": "add",
                    "table": args.table,
                    "column": args.column,
                    "changed": False,
                }
            statement = sql.SQL("ALTER TABLE {} ADD COLUMN {} {}{}").format(
                table(args.table),
                sql.Identifier(args.column),
                sql.SQL(TYPES[args.type]),
                default,
            )
        elif args.action == "set-default":
            if not exists:
                raise ValueError("Test column does not exist; add it first")
            statement = sql.SQL(
                "ALTER TABLE {} ALTER COLUMN {} SET DEFAULT {}::{}"
            ).format(
                table(args.table),
                sql.Identifier(args.column),
                sql.Literal(args.default),
                sql.SQL(TYPES[args.type]),
            )
        else:
            statement = sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS {}").format(
                table(args.table), sql.Identifier(args.column)
            )
        connection.execute(statement)
    return {
        "operation": "alter",
        "action": args.action,
        "table": args.table,
        "column": args.column,
        "changed": args.action != "drop" or exists,
    }


def status(connection):
    with connection.transaction():
        identity = connection.execute(
            "SELECT current_database() AS database, current_user AS username"
        ).fetchone()
        counts = {}
        for name in TABLES:
            counts[name] = connection.execute(
                sql.SQL("SELECT count(*) AS count FROM {}").format(table(name))
            ).fetchone()["count"]
    return {
        "operation": "status",
        **identity,
        "schema": os.environ.get("PGSCHEMA", "public"),
        "rows": counts,
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="operation", required=True)
    for operation in ("insert", "update"):
        command = commands.add_parser(operation)
        command.add_argument(
            "--count",
            type=bounded_count,
            default=10,
            help="Customer groups per committed batch (1-10000)",
        )
        command.add_argument(
            "--run-id",
            type=run_id,
            help="Fixture identifier; update defaults to all generated rows",
        )
    alter = commands.add_parser(
        "alter", help="Modify only explicitly named cdc_test_ columns"
    )
    alter.add_argument("--table", choices=TABLES, default="customers")
    alter.add_argument("--column", type=test_column, default="cdc_test_note")
    alter.add_argument(
        "--action", choices=("add", "set-default", "drop"), default="add"
    )
    alter.add_argument("--type", choices=TYPES, default="text")
    alter.add_argument(
        "--default", help="Literal default value; PostgreSQL casts to the selected type"
    )
    run = commands.add_parser(
        "run",
        help="Insert and update in separate committed transactions for a bounded duration",
    )
    run.add_argument(
        "--duration",
        type=positive_seconds,
        default=60,
        help="Seconds to run (max 86400)",
    )
    run.add_argument(
        "--interval", type=positive_seconds, default=1, help="Seconds between batches"
    )
    run.add_argument("--batch-size", type=bounded_count, default=5)
    run.add_argument("--run-id", type=run_id)
    commands.add_parser("status", help="Read-only connection and table inventory check")
    commands.add_parser(
        "idle", help="Keep the tools container running without generating changes"
    )
    return root


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if (
        args.operation == "alter"
        and args.action == "set-default"
        and args.default is None
    ):
        command.error("--action set-default requires --default")
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        if args.operation == "idle":
            emit(
                operation="idle",
                message="Ready. Run insert.py, update.py, alter.py or simulate.py with docker "
                "compose exec.",
            )
            stopped.wait()
            return 0
        with connect() as connection:
            if args.operation == "insert":
                emit(
                    **insert_batch(
                        connection, args.count, args.run_id or uuid.uuid4().hex
                    )
                )
            elif args.operation == "update":
                emit(**update_batch(connection, args.count, args.run_id))
            elif args.operation == "alter":
                emit(**alter_schema(connection, args))
            elif args.operation == "status":
                emit(**status(connection))
            else:
                marker = args.run_id or uuid.uuid4().hex
                deadline = time.monotonic() + args.duration
                batches = 0
                while not stopped.is_set() and time.monotonic() < deadline:
                    emit(**insert_batch(connection, args.batch_size, marker))
                    emit(**update_batch(connection, args.batch_size, marker))
                    batches += 1
                    stopped.wait(
                        max(0, min(args.interval, deadline - time.monotonic()))
                    )
                emit(
                    operation="run-complete",
                    run_id=marker,
                    batches=batches,
                    inserted_rows=batches * args.batch_size * 3,
                )
        return 0
    except psycopg.Error as error:
        # Do not echo connection strings, passwords or database exception details.
        emit(
            operation=args.operation,
            error="Database operation failed",
            sqlstate=error.sqlstate,
            hint="Check source connectivity, credentials, table schema and column defaults.",
        )
        return 1
    except ValueError as error:
        emit(operation=args.operation, error=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
