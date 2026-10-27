import time
from builtins import list as List
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import structlog

from app.core.config import get_settings
from app.core.errors import DomainError, redact

log = structlog.get_logger()


@dataclass(frozen=True)
class TaskFailure:
    code: str
    category: str
    retryable: bool
    message: str


def classify_task_failure(trace: object, connector_type: str = "sink") -> TaskFailure:
    """Classify a Connect trace once, then discard it before it crosses the adapter boundary."""
    text = str(trace).lower()
    if connector_type == "source":
        hint = failure_hint(trace, connector_type)
        return TaskFailure("SOURCE_TASK_FAILED", "SOURCE_FAILURE", False, hint)

    permanent: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
        (
            ("constraintviolationexception", "violates check constraint", "duplicate key"),
            "DESTINATION_CONSTRAINT_VIOLATION",
            "CONSTRAINT_VIOLATION",
            "Destination constraint violation prevented delivery",
        ),
        (
            (
                "password authentication failed",
                "authenticationexception",
                "access denied for user",
                "invalid authorization specification",
                "sqlstate 28",
            ),
            "DESTINATION_AUTHENTICATION_FAILED",
            "AUTHENTICATION",
            "Destination authentication failed",
        ),
        (
            ("permission denied", "not authorized", "insufficient privilege", "sqlstate 42501"),
            "DESTINATION_PERMISSION_DENIED",
            "PERMISSION",
            "Destination permissions do not allow delivery",
        ),
        (
            ("does not exist", "unknown column", "unknown table"),
            "DESTINATION_MAPPING_INVALID",
            "INVALID_MAPPING",
            "Destination table or column does not exist",
        ),
        (
            ("cluecdc change record does not match", "unsupported database delivery type"),
            "DESTINATION_SCHEMA_INCOMPATIBLE",
            "SCHEMA_INCOMPATIBILITY",
            "Source record does not match delivery types; refresh discovery and mappings",
        ),
    )
    for needles, code, category, message in permanent:
        if any(needle in text for needle in needles):
            return TaskFailure(code, category, False, message)

    transient: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
        (
            (
                "sqlstate 57p01",
                "sqlstate: 57p01",
                "sqlstate 57p02",
                "sqlstate: 57p02",
                "sqlstate 57p03",
                "sqlstate: 57p03",
                "admin shutdown",
                "crash shutdown",
                "cannot connect now",
                "terminating connection due to administrator command",
            ),
            "DESTINATION_DATABASE_RESTART",
            "TRANSIENT_DATABASE_UNAVAILABLE",
            "Destination database restarted or is temporarily unavailable",
        ),
        (
            (
                "connection refused",
                "connectexception: connection",
                "jdbcconnectionexception",
                "communications link failure",
                "connection reset",
                "connection is closed",
                "server closed the connection",
                "could not connect",
                "sqlstate 08",
            ),
            "DESTINATION_CONNECTION_UNAVAILABLE",
            "TRANSIENT_CONNECTIVITY",
            "Destination database connection failed",
        ),
        (
            ("timeout", "timed out", "sockettimeoutexception"),
            "DESTINATION_CONNECTION_TIMEOUT",
            "TRANSIENT_TIMEOUT",
            "Destination database connection timed out",
        ),
    )
    for needles, code, category, message in transient:
        if any(needle in text for needle in needles):
            return TaskFailure(code, category, True, message)
    return TaskFailure(
        "CONNECT_TASK_FAILED",
        "UNKNOWN",
        False,
        "Task failed; inspect Kafka Connect worker logs",
    )


def failure_hint(trace: object, connector_type: str = "sink") -> str:
    text = str(trace).lower()
    if connector_type == "source":
        source_hints = [
            (
                (
                    "requested wal segment has already been removed",
                    "replication slot does not exist",
                    "wal position is no longer available",
                ),
                "PostgreSQL replication position is unavailable; restore retained WAL/slot "
                "or plan an explicit resnapshot",
            ),
            (
                (
                    "could not find first log file name in binary log index file",
                    "the connector is trying to read binlog starting at",
                    "binlog event is not available",
                    "purged binary log",
                ),
                "MySQL binlog position is no longer retained; restore the binlog or plan "
                "an explicit resnapshot",
            ),
            (
                ("schema history", "database history topic"),
                "Debezium schema history is unavailable; restore the history topic or plan "
                "an explicit resnapshot",
            ),
            (
                ("offset out of range", "no offset available"),
                "Kafka source offset is unavailable; inspect Kafka retention before an "
                "explicit resnapshot",
            ),
        ]
        for needles, message in source_hints:
            if any(needle in text for needle in needles):
                return message
        return "Source task failed; inspect Kafka Connect worker logs"
    return classify_task_failure(trace, connector_type).message


class KafkaConnectClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client

    async def request(self, method: str, path: str, payload=None, secrets=None):
        started = time.monotonic()
        result = "failed"
        try:
            if self.client:
                response = await self.client.request(method, self.base_url + path, json=payload)
            else:
                async with httpx.AsyncClient(
                    timeout=get_settings().integration_timeout_seconds
                ) as client:
                    response = await client.request(method, self.base_url + path, json=payload)
            if response.status_code == 404:
                raise DomainError(
                    "CONNECTOR_NOT_FOUND", "Connector was not found in Kafka Connect", 404
                )
            if response.status_code == 409:
                raise DomainError(
                    "CONNECT_CONFLICT",
                    "Kafka Connect is rebalancing or the connector already exists",
                    409,
                )
            if response.status_code >= 400:
                raise DomainError(
                    "CONNECT_REQUEST_FAILED",
                    "Kafka Connect rejected the request",
                    502,
                    {"upstream_status": response.status_code},
                )
            if not response.content:
                result = "success"
                return {}
            try:
                data = response.json()
            except ValueError as exc:
                raise DomainError(
                    "CONNECT_INVALID_RESPONSE", "Kafka Connect returned malformed JSON", 502
                ) from exc
            result = "success"
            return redact(data, secrets) if secrets else data

        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise DomainError(
                "CONNECT_UNAVAILABLE", "Kafka Connect is unavailable or timed out", 503
            ) from exc
        finally:
            log.info(
                "connector_operation",
                connect_cluster=self.base_url,
                operation=f"{method} {path.rsplit('/', 1)[-1].split('?')[0]}",
                connector_name=path.split("/")[2]
                if path.startswith("/connectors/")
                else payload.get("name")
                if isinstance(payload, dict)
                else None,
                result=result,
                duration=round(time.monotonic() - started, 3),
            )

    async def create_idempotent(self, name: str, config: dict) -> bool:
        """Create a connector or adopt an exact deterministic prior attempt."""
        try:
            await self.create(name, config)
            return True
        except DomainError as error:
            if error.code != "CONNECT_CONFLICT":
                raise
        try:
            deployed = await self.raw_config(name)
        except DomainError:
            raise DomainError(
                "CONNECT_DEPLOY_CONFLICT",
                "Kafka Connect is busy or the connector name is already reserved",
                409,
            ) from None
        if deployed != config:
            raise DomainError(
                "CONNECT_DEPLOY_CONFLICT",
                "An existing connector with this name has different configuration",
                409,
            )
        return False

    @staticmethod
    def path(name: str) -> str:
        return "/connectors/" + quote(name, safe="")

    async def list(self):
        return await self.request("GET", "/connectors")

    async def plugins(self) -> List[dict]:
        data = await self.request("GET", "/connector-plugins")
        if not isinstance(data, list) or any(
            not isinstance(plugin, dict) or not isinstance(plugin.get("class"), str)
            for plugin in data
        ):
            raise DomainError(
                "CONNECT_INVALID_RESPONSE", "Connect plugin response was malformed", 502
            )
        return data

    async def info(self, name: str):
        return await self.request("GET", self.path(name))

    async def raw_config(self, name: str) -> dict:
        data = await self.request("GET", self.path(name) + "/config")
        if not isinstance(data, dict):
            raise DomainError(
                "CONNECT_INVALID_RESPONSE", "Connector configuration was malformed", 502
            )
        return data

    async def config(self, name: str):
        return redact(await self.raw_config(name))

    async def validate(self, config: dict, secrets: List[str] | None = None):
        password = config.get("database.password") or config.get("connection.password", "")
        data = await self.request(
            "PUT",
            "/connector-plugins/" + quote(config["connector.class"], safe="") + "/config/validate",
            config,
            [password, *(secrets or [])],
        )
        if not isinstance(data, dict) or "error_count" not in data:
            raise DomainError(
                "CONNECT_INVALID_RESPONSE", "Connect validation response was malformed", 502
            )
        if data["error_count"]:
            errors = [
                {
                    "name": c.get("value", {}).get("name"),
                    "errors": c.get("value", {}).get("errors", []),
                }
                for c in data.get("configs", [])
                if c.get("value", {}).get("errors")
            ]
            raise DomainError(
                "CONNECTOR_CONFIG_INVALID",
                "Connector configuration validation failed",
                422,
                {"errors": errors},
            )
        return {"valid": True}

    async def create(self, name: str, config: dict):
        return await self.request(
            "POST",
            "/connectors",
            {"name": name, "config": config},
            [config.get("database.password") or config.get("connection.password", "")],
        )

    async def update(self, name: str, config: dict):
        return await self.request(
            "PUT",
            self.path(name) + "/config",
            config,
            [config.get("database.password") or config.get("connection.password", "")],
        )

    async def status(self, name: str):
        data = await self.request("GET", self.path(name) + "/status")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("connector"), dict)
            or not isinstance(data.get("tasks"), list)
        ):
            raise DomainError(
                "CONNECT_INVALID_RESPONSE", "Connector status response was malformed", 502
            )
        # Traces can contain credentials; expose state and task identity, never raw trace.
        for task in data["tasks"]:
            trace = task.pop("trace", None)
            if trace:
                failure = classify_task_failure(trace, str(data.get("type", "sink")))
                task.update(
                    error=failure.message,
                    error_code=failure.code,
                    error_category=failure.category,
                    retryable=failure.retryable,
                )
        if data["connector"].pop("trace", None):
            data["connector"]["error"] = "Connector failed; inspect Kafka Connect worker logs"
        return data

    async def operate(self, name: str, operation: str, task: int | None = None):
        if operation in {"pause", "resume", "stop"}:
            return await self.request("PUT", self.path(name) + "/" + operation)
        if operation == "restart":
            return await self.request(
                "POST", self.path(name) + "/restart?includeTasks=true&onlyFailed=false"
            )
        if operation == "restart-task" and task is not None:
            return await self.request("POST", self.path(name) + f"/tasks/{task}/restart")
        if operation == "delete":
            return await self.request("DELETE", self.path(name))
        raise DomainError("INVALID_OPERATION", "Unsupported connector operation")
