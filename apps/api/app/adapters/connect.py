import time
from builtins import list as List
from urllib.parse import quote

import httpx
import structlog

from app.core.config import get_settings
from app.core.errors import DomainError, redact

log = structlog.get_logger()


def failure_hint(trace: object) -> str:
    text = str(trace).lower()
    hints = [
        (
            ("constraintviolationexception", "violates check constraint", "duplicate key"),
            "Destination constraint violation prevented delivery",
        ),
        (
            ("password authentication failed", "authenticationexception"),
            "Destination authentication failed",
        ),
        (
            ("connection refused", "connectexception: connection", "jdbcconnectionexception"),
            "Destination database connection failed",
        ),
        (("does not exist",), "Destination table or column does not exist"),
        (
            ("cluecdc change record does not match",),
            "Source record does not match delivery types; refresh discovery and mappings",
        ),
    ]
    for needles, message in hints:
        if any(needle in text for needle in needles):
            return message
    return "Task failed; inspect Kafka Connect worker logs"


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

    async def config(self, name: str):
        return redact(await self.request("GET", self.path(name) + "/config"))

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
                task["error"] = (
                    failure_hint(trace)
                    if data.get("type") == "sink"
                    else "Task failed; inspect Kafka Connect worker logs"
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
