"""Create a bounded, repeatable set of demo tables in both CDC source databases."""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pymysql
from psycopg import sql

TABLE_STEMS = (
    "accounts",
    "addresses",
    "audit_events",
    "carts",
    "categories",
    "coupons",
    "devices",
    "inventory",
    "invoices",
    "messages",
    "notifications",
    "products",
    "product_reviews",
    "promotions",
    "refunds",
    "returns",
    "sessions",
    "shipments",
    "subscriptions",
    "suppliers",
    "support_tickets",
    "warehouses",
    "wishlists",
    "workflow_runs",
)


def emit(**values):
    print(json.dumps(values, default=str), flush=True)


def bounded(minimum, maximum, label):
    def parse(value):
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{label} must be between {minimum} and {maximum}"
            )
        return parsed

    return parse


def safe_prefix(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,19}", value):
        raise argparse.ArgumentTypeError(
            "prefix must start with a lowercase letter and contain at most 20 "
            "lowercase letters, digits or underscores"
        )
    return value


def table_names(prefix, count):
    return [
        f"{prefix}_{TABLE_STEMS[index]}"
        if index < len(TABLE_STEMS)
        else f"{prefix}_entity_{index + 1:02d}"
        for index in range(count)
    ]


def rows_for(table_index, count):
    base = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        (
            f"{table_index + 1:02d}-{row + 1:06d}",
            (row % 25) + 1,
            f"Demo record {row + 1}",
            ("active", "pending", "archived")[row % 3],
            Decimal(f"{(row + 1) * (table_index + 1) % 10000}.{row % 100:02d}"),
            round(((row * 17) % 1000) / 10, 2),
            row % 5 != 0,
            json.dumps(
                {
                    "fixture": "cluecdc-demo",
                    "sequence": row + 1,
                    "tags": [f"group-{row % 7}", f"table-{table_index + 1}"],
                }
            ),
            f"Generated fixture row {row + 1} for UI and CDC load testing",
            base + timedelta(seconds=(table_index * count) + row),
        )
        for row in range(count)
    ]


def seed_postgresql(names, row_count, reset):
    schema = os.environ.get("PGSCHEMA", "public")
    connection = psycopg.connect(
        connect_timeout=10,
        application_name="cluecdc-demo-table-seeder",
        options="-c statement_timeout=120000 -c lock_timeout=10000",
    )
    try:
        with connection.transaction():
            for index, name in enumerate(names):
                target = sql.Identifier(schema, name)
                if reset:
                    connection.execute(
                        sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(target)
                    )
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} ("
                        "id bigserial PRIMARY KEY, external_id varchar(32) NOT NULL UNIQUE, "
                        "tenant_id integer NOT NULL, name varchar(255) NOT NULL, "
                        "status varchar(32) NOT NULL, amount numeric(14,2) NOT NULL, "
                        "score double precision NOT NULL, is_active boolean NOT NULL, "
                        "payload jsonb NOT NULL, notes text, event_at timestamptz NOT NULL, "
                        "created_at timestamptz NOT NULL DEFAULT now(), "
                        "updated_at timestamptz NOT NULL DEFAULT now())"
                    ).format(target)
                )
                connection.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (tenant_id, status)"
                    ).format(sql.Identifier(f"ix_{name}_tenant_status"), target)
                )
                with connection.cursor() as cursor:
                    cursor.executemany(
                        sql.SQL(
                            "INSERT INTO {} (external_id,tenant_id,name,status,amount,score,"
                            "is_active,payload,notes,event_at) VALUES (%s,%s,%s,%s,%s,%s,%s,"
                            "%s::jsonb,%s,%s) ON CONFLICT (external_id) DO UPDATE SET "
                            "name=EXCLUDED.name,status=EXCLUDED.status,amount=EXCLUDED.amount,"
                            "score=EXCLUDED.score,is_active=EXCLUDED.is_active,"
                            "payload=EXCLUDED.payload,notes=EXCLUDED.notes,event_at=EXCLUDED.event_at,"
                            "updated_at=now()"
                        ).format(target),
                        rows_for(index, row_count),
                    )
                connection.execute(
                    sql.SQL("ALTER TABLE {} REPLICA IDENTITY FULL").format(target)
                )
        return {
            "database": "postgresql",
            "tables": len(names),
            "rows_per_table": row_count,
        }
    finally:
        connection.close()


def seed_mysql(names, row_count, reset):
    connection = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "cdc-source-mysql"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "cdc_mysql"),
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ.get("MYSQL_DATABASE", "shop"),
        connect_timeout=10,
        read_timeout=120,
        write_timeout=120,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            for index, name in enumerate(names):
                quoted = f"`{name}`"
                if reset:
                    cursor.execute(f"DROP TABLE IF EXISTS {quoted}")
                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {quoted} ("
                    "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, "
                    "external_id VARCHAR(32) NOT NULL UNIQUE, tenant_id INT NOT NULL, "
                    "name VARCHAR(255) NOT NULL, status VARCHAR(32) NOT NULL, "
                    "amount DECIMAL(14,2) NOT NULL, score DOUBLE NOT NULL, "
                    "is_active BOOLEAN NOT NULL, payload JSON NOT NULL, notes TEXT NULL, "
                    "event_at DATETIME(6) NOT NULL, created_at DATETIME(6) NOT NULL "
                    "DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL "
                    "DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), "
                    f"INDEX `ix_{name}_tenant_status` (tenant_id,status)) ENGINE=InnoDB"
                )
                mysql_rows = [
                    row[:-1] + (row[-1].replace(tzinfo=None),)
                    for row in rows_for(index, row_count)
                ]
                cursor.executemany(
                    f"INSERT INTO {quoted} (external_id,tenant_id,name,status,amount,score,"
                    "is_active,payload,notes,event_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "AS incoming ON DUPLICATE KEY UPDATE name=incoming.name,status=incoming.status,"
                    "amount=incoming.amount,score=incoming.score,is_active=incoming.is_active,"
                    "payload=incoming.payload,notes=incoming.notes,event_at=incoming.event_at",
                    mysql_rows,
                )
        connection.commit()
        return {"database": "mysql", "tables": len(names), "rows_per_table": row_count}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--database", choices=("all", "postgresql", "mysql"), default="all"
    )
    root.add_argument("--tables", type=bounded(1, 60, "tables"), default=24)
    root.add_argument("--rows", type=bounded(1, 10000, "rows"), default=250)
    root.add_argument("--prefix", type=safe_prefix, default="demo")
    root.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate only tables matching this invocation's generated names",
    )
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    names = table_names(args.prefix, args.tables)
    results = []
    try:
        if args.database in {"all", "postgresql"}:
            results.append(seed_postgresql(names, args.rows, args.reset))
        if args.database in {"all", "mysql"}:
            results.append(seed_mysql(names, args.rows, args.reset))
        emit(operation="seed-demo-tables", prefix=args.prefix, results=results)
        return 0
    except (
        psycopg.Error,
        pymysql.MySQLError,
        KeyError,
        RuntimeError,
        ValueError,
    ) as error:
        emit(
            operation="seed-demo-tables",
            error=type(error).__name__,
            hint="Check source services, credentials and workload environment variables.",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
