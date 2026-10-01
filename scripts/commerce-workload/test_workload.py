"""Real PostgreSQL regression checks, isolated in a rolled-back test schema."""

import contextlib
import io
import os
import unittest
import uuid

from generate import (
    alter_schema,
    connect,
    insert_batch,
    parser,
    status,
    table,
    update_batch,
)
from psycopg import sql
from seed_demo_tables import parser as demo_parser
from seed_demo_tables import table_names


class WorkloadTests(unittest.TestCase):
    def test_customer_order_payment_changes_and_schema_evolution(self):
        original_schema = os.environ.get("PGSCHEMA")
        schema = "cdc_workload_test_" + uuid.uuid4().hex
        try:
            os.environ["PGSCHEMA"] = schema
            with connect() as connection, connection.transaction(force_rollback=True):
                connection.execute(
                    sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
                )
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE {} (id bigserial PRIMARY KEY, name text NOT NULL, email "
                        "text NOT NULL UNIQUE, updated_at timestamptz NOT NULL DEFAULT now())"
                    ).format(table("customers"))
                )
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE {} (id bigserial PRIMARY KEY, customer_id bigint NOT "
                        "NULL REFERENCES {}(id), total numeric(12,2) NOT NULL, status text NOT "
                        "NULL DEFAULT 'pending', updated_at timestamptz NOT NULL DEFAULT now())"
                    ).format(table("orders"), table("customers"))
                )
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE {} (id bigserial PRIMARY KEY, order_id bigint NOT NULL "
                        "REFERENCES {}(id), amount numeric(12,2) NOT NULL, status text NOT "
                        "NULL DEFAULT 'pending')"
                    ).format(table("payments"), table("orders"))
                )
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} (name,email) VALUES ('Original "
                        "shopper','original@example.test')"
                    ).format(table("customers"))
                )
                inserted = insert_batch(connection, 2, "smoke")
                self.assertEqual(
                    (inserted["customers"], inserted["orders"], inserted["payments"]),
                    (2, 2, 2),
                )
                insert_batch(connection, 1, "smoke-other")
                updated = update_batch(connection, 10, "smoke")
                self.assertEqual(
                    (updated["customers"], updated["orders"], updated["payments"]),
                    (2, 2, 2),
                )
                self.assertEqual(update_batch(connection, 2, "absent")["customers"], 0)
                original = connection.execute(
                    sql.SQL(
                        "SELECT name FROM {} WHERE email='original@example.test'"
                    ).format(table("customers"))
                ).fetchone()
                self.assertEqual(original["name"], "Original shopper")
                other = connection.execute(
                    sql.SQL("SELECT name FROM {} WHERE email LIKE %s").format(
                        table("customers")
                    ),
                    ("cdc-load-smoke-other.%",),
                ).fetchone()
                self.assertEqual(other["name"], "CDC shopper smoke-other")
                mismatches = connection.execute(
                    sql.SQL(
                        "SELECT count(*) AS count FROM {} p JOIN {} o ON o.id=p.order_id WHERE "
                        "p.amount<>o.total"
                    ).format(table("payments"), table("orders"))
                ).fetchone()
                self.assertEqual(mismatches["count"], 0)
                updated_again = update_batch(connection, 2, "smoke")
                self.assertEqual(updated_again["orders"], 2)
                changed = connection.execute(
                    sql.SQL("SELECT status FROM {} WHERE id=%s").format(
                        table("orders")
                    ),
                    (inserted["first_ids"]["order_id"],),
                ).fetchone()
                self.assertEqual(changed["status"], "shipped")
                args = parser().parse_args(["alter", "--default", "shopper's note"])
                self.assertTrue(alter_schema(connection, args)["changed"])
                self.assertFalse(alter_schema(connection, args)["changed"])
                insert_batch(connection, 1, "schema")
                note = connection.execute(
                    sql.SQL("SELECT cdc_test_note FROM {} WHERE email LIKE %s").format(
                        table("customers")
                    ),
                    ("cdc-load-schema.%",),
                ).fetchone()
                self.assertEqual(note["cdc_test_note"], "shopper's note")
                args = parser().parse_args(
                    ["alter", "--action", "set-default", "--default", "revised"]
                )
                self.assertTrue(alter_schema(connection, args)["changed"])
                args = parser().parse_args(["alter", "--action", "drop"])
                self.assertTrue(alter_schema(connection, args)["changed"])
                self.assertFalse(alter_schema(connection, args)["changed"])
                self.assertEqual(
                    status(connection)["rows"],
                    {"customers": 5, "orders": 4, "payments": 4},
                )
        finally:
            if original_schema is None:
                os.environ.pop("PGSCHEMA", None)
            else:
                os.environ["PGSCHEMA"] = original_schema

    def test_invalid_inputs_rejected_before_a_database_operation(self):
        for argv in (
            ["alter", "--column", "email"],
            ["insert", "--run-id", "bad%"],
            ["insert", "--count", "0"],
            ["run", "--duration", "nan"],
            ["run", "--interval", "0"],
        ):
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                parser().parse_args(argv)
            self.assertEqual(error.exception.code, 2)

    def test_demo_table_inventory_is_bounded_and_predictable(self):
        args = demo_parser().parse_args([])
        names = table_names(args.prefix, args.tables)
        self.assertEqual(len(names), 24)
        self.assertEqual(names[0], "demo_accounts")
        self.assertEqual(names[-1], "demo_workflow_runs")
        self.assertEqual(len(names), len(set(names)))

        for argv in (("--tables", "0"), ("--rows", "10001"), ("--prefix", "bad-name")):
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                demo_parser().parse_args(argv)


if __name__ == "__main__":
    unittest.main()
