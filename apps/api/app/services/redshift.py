import asyncio
import uuid
from contextlib import closing

from app.core.errors import DomainError
from app.models.entities import Connection


def _aws_credentials(credentials: dict[str, str]) -> dict:
    values = {}
    for target, source in (
        ("aws_access_key_id", "aws_access_key_id"),
        ("aws_secret_access_key", "aws_secret_access_key"),
        ("aws_session_token", "aws_session_token"),
    ):
        if credentials.get(source):
            values[target] = credentials[source]
    return values


def connection_options(connection: Connection, credentials: dict[str, str]) -> dict:
    config = connection.config_json
    options = {
        "host": config["host"],
        "port": config["port"],
        "database": config["database_name"],
        "user": config["username"],
        "ssl": True,
        "timeout": 15,
    }
    if config["authentication"] == "PASSWORD":
        options["password"] = credentials["password"]
    else:
        options["iam"] = True
        if config.get("serverless"):
            options.update(
                is_serverless=True,
                serverless_work_group=config.get("workgroup_name"),
            )
        else:
            options["cluster_identifier"] = config.get("cluster_identifier")
        options.update(_aws_credentials(credentials))
    return options


def _test_sql(connection: Connection, credentials: dict[str, str]) -> None:
    import redshift_connector

    config = connection.config_json
    options = connection_options(connection, credentials)
    with closing(redshift_connector.connect(**options)) as database:
        cursor = database.cursor()
        try:
            cursor.execute(
                "SELECT current_database(), current_user, "
                "has_schema_privilege(current_user, %s, 'USAGE'), "
                "has_schema_privilege(current_user, %s, 'CREATE')",
                (config["schema_name"], config["schema_name"]),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    if not row or row[0] != config["database_name"]:
        raise DomainError(
            "REDSHIFT_DATABASE_MISMATCH", "Connected to an unexpected Redshift database", 422
        )
    if not row[2] or not row[3]:
        raise DomainError(
            "REDSHIFT_PERMISSION_DENIED",
            "The Redshift identity needs USAGE and CREATE on the target schema",
            422,
        )


def _test_s3(connection: Connection, credentials: dict[str, str]) -> None:
    import boto3

    config = connection.config_json
    client = boto3.client("s3", region_name=config["region"], **_aws_credentials(credentials))
    key = f"{config['staging_prefix']}/.cluecdc/connection-tests/{uuid.uuid4().hex}.probe"
    uploaded = False
    try:
        client.put_object(Bucket=config["s3_bucket"], Key=key, Body=b"cluecdc\n")
        uploaded = True
        client.head_object(Bucket=config["s3_bucket"], Key=key)
        client.delete_object(Bucket=config["s3_bucket"], Key=key)
        uploaded = False
    finally:
        if uploaded:
            client.delete_object(Bucket=config["s3_bucket"], Key=key)


async def test_connection(connection: Connection, credentials: dict[str, str]) -> dict:
    try:
        await asyncio.to_thread(_test_sql, connection, credentials)
        await asyncio.to_thread(_test_s3, connection, credentials)
    except DomainError:
        raise
    except Exception as exc:
        text = str(exc).lower()
        if any(
            value in text for value in ("password", "authentication", "permission", "accessdenied")
        ):
            raise DomainError(
                "REDSHIFT_PERMISSION_DENIED",
                "Redshift or S3 rejected the configured identity",
                422,
            ) from exc
        raise DomainError(
            "REDSHIFT_UNAVAILABLE", "Redshift or S3 staging validation failed", 503
        ) from exc
    return {
        "success": True,
        "status": "HEALTHY",
        "checks": [
            {"name": "sql_connectivity", "status": "success"},
            {"name": "schema_permissions", "status": "success"},
            {"name": "s3_staging_access", "status": "success"},
            {"name": "staging_cleanup", "status": "success"},
        ],
    }
