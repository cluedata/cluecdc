"""Document discovery and read-only change-stream diagnostics. No SQL emulation."""

from contextlib import asynccontextmanager

from app.adapters.source_sql import check, diagnostics
from app.core.errors import DomainError
from app.providers.mongo_uri import validate_uri


class MongoDBAdapter:
    def __init__(self, source, connection_uri):
        self.source, self.connection_uri = source, connection_uri

    @asynccontextmanager
    async def connection(self):
        client = None
        try:
            from pymongo import AsyncMongoClient
            from pymongo.errors import OperationFailure

            validate_uri(self.connection_uri, self.source.ssl_enabled)
            timeout = int(self.source.provider_options.get("connection_timeout_seconds", 10)) * 1000
            client = AsyncMongoClient(self.connection_uri, tls=self.source.ssl_enabled,
                tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False,
                serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout, socketTimeoutMS=timeout)
            yield client
        except DomainError:
            raise
        except ImportError:
            raise DomainError("SOURCE_DRIVER_MISSING", "Install pymongo on the API/worker image", 422) from None
        except Exception as error:
            # Driver exceptions can contain full credentials/URI. Never chain or return them.
            if isinstance(error, OperationFailure) and error.code in {13, 18}:
                code = "SOURCE_AUTH_FAILED" if error.code == 18 else "SOURCE_PERMISSION_DENIED"
                raise DomainError(code, "MongoDB authentication or change-stream permissions failed", 422) from None
            if any(word in str(error).lower() for word in ("certificate", "ssl", "tls")):
                raise DomainError("SSL_ERROR", "MongoDB TLS certificate verification failed", 422) from None
            raise DomainError("SOURCE_UNAVAILABLE", "MongoDB connection or discovery failed; verify URI and topology", 503) from None
        finally:
            if client is not None:
                await client.close()

    async def test(self):
        async with self.connection() as client:
            await client.admin.command("ping")
        return {"success": True, "status": "HEALTHY", "checks": [
            check("connection", True, True, True, "Verify network and TLS"),
            check("authentication", True, True, True, "Verify URI authentication") ]}

    async def readiness(self):
        async with self.connection() as client:
            hello = await client.admin.command("hello")
            info = await client.admin.command("buildInfo")
            major = int(info["version"].split(".")[0])
            replica = bool(hello.get("setName"))
            checks = [check("connection", True, True, True, "Check network"),
                check("server_version", info["version"], "6.x/7.x/8.x", major in {6, 7, 8}, "Use a compatible MongoDB release"),
                check("replica_set", hello.get("setName"), "Replica set", replica,
                    "Convert standalone to a replica set; sharded topology is not verified")]
            database = client[self.source.database_name]
            try:
                names = await database.list_collection_names()
                checks.append(check("collection_discovery", len(names), "Authorized", True, "Grant listCollections"))
            except Exception:
                checks.append(check("collection_discovery", None, "Authorized", False, "Grant listCollections and find on the configured database"))
            if replica:
                try:
                    stream = await database.watch(max_await_time_ms=1000)
                    await stream.close()
                    checks.append(check("change_streams", True, True, True, "Grant changeStream and find"))
                except Exception:
                    checks.append(check("change_streams", None, True, False,
                        "Grant changeStream and find on the configured database; verify replica topology"))
            retention = None
            try:
                first = await client.local["oplog.rs"].find_one(sort=[("$natural", 1)])
                last = await client.local["oplog.rs"].find_one(sort=[("$natural", -1)])
                if first and last:
                    retention = max(0, last["ts"].time - first["ts"].time)
            except Exception:
                pass
            checks.append(check("oplog_window_seconds", retention, "Longer than maximum outage", False,
                "Monitor oplog window externally if local.oplog.rs is restricted (including Atlas). "
                "Expired resume tokens require operator reconciliation; offsets will not be reset.", True))
            return {**diagnostics(checks), "version": info["version"],
                "oplog_window_seconds": retention, "monitoring_limited": retention is None,
                "resume_tokens": "Debezium-managed; compatibility of existing tokens requires runtime observation"}

    async def list_databases(self):
        async with self.connection() as client:
            return await client.list_database_names()

    async def inspect_table(self, schema_name, table_name):
        if schema_name != self.source.database_name or table_name.startswith("system."):
            return {"ready": False, "issues": ["Select a collection in the configured database"]}
        async with self.connection() as client:
            cursor = await client[schema_name].list_collections(filter={"name": table_name})
            collections = await cursor.to_list(length=1)
            exists = bool(collections and collections[0].get("type") == "collection")
            return {"ready": exists, "primary_key_columns": ["_id"],
                "columns": [{"name": "_id", "type": "BSON", "nullable": False, "ordinal": 1}],
                "indexes": [], "source_properties": {"collection_kind": "DOCUMENT",
                    "document_identity": "_id", "event_format": "BSON_EXTENDED_JSON",
                    "update_semantics": "PATCH", "full_document_upsert": False},
                "estimated_rows": None, "estimated_size_bytes": None, "checks": [],
                "issues": [] if exists else ["Collection missing, a view, or not authorized"], "warnings": []}

    async def discover(self):
        async with self.connection() as client:
            cursor = await client[self.source.database_name].list_collections()
            metadata = await cursor.to_list(length=501)
        if len(metadata) > 500:
            raise DomainError("DISCOVERY_LIMIT", "Select a database with at most 500 collections", 422)
        collections = []
        for item in metadata:
            if item.get("type") != "collection" or item["name"].startswith("system."):
                continue
            info = await self.inspect_table(self.source.database_name, item["name"])
            collections.append({"schema_name": self.source.database_name, "table_name": item["name"],
                "primary_key_columns": info["primary_key_columns"], "columns_json": info["columns"],
                "indexes_json": [], "source_properties_json": info["source_properties"],
                "estimated_rows": None, "estimated_size_bytes": None, "cdc_ready": info["ready"],
                "cdc_status": "READY" if info["ready"] else "BLOCKED", "cdc_issues": info["issues"]})
        return collections

    async def validate_custom_key(self, schema_name, table_name, key_columns):
        if key_columns != ["_id"]:
            return {"status": "INVALID", "verified": False, "enforced": False,
                    "errors": ["MongoDB capture identity must be _id"]}
        return {"status": "DATABASE_ENFORCED", "verified": True, "enforced": True,
                "constraint_name": "_id_", "errors": []}
