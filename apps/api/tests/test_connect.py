import httpx
import pytest

from app.adapters.connect import KafkaConnectClient, classify_task_failure, failure_hint
from app.core.errors import DomainError


async def test_connect_timeout_is_explicit():
    def handler(request):
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DomainError) as exc:
            await KafkaConnectClient("http://connect", http).list()
        assert exc.value.code == "CONNECT_UNAVAILABLE"


async def test_connect_validation_errors_redact_echoed_password():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "error_count": 1,
                "configs": [
                    {"value": {"name": "database.user", "errors": ["rejected private-password"]}}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DomainError) as exc:
            await KafkaConnectClient("http://connect", http).validate(
                {"connector.class": "postgres", "database.password": "private-password"}
            )
        assert exc.value.code == "CONNECTOR_CONFIG_INVALID"
        assert "private-password" not in str(exc.value.details)


@pytest.mark.parametrize(
    "status,code",
    [(404, "CONNECTOR_NOT_FOUND"), (409, "CONNECT_CONFLICT"), (500, "CONNECT_REQUEST_FAILED")],
)
async def test_upstream_errors(status, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status))
    ) as http:
        with pytest.raises(DomainError) as exc:
            await KafkaConnectClient("http://connect", http).status("capture")
        assert exc.value.code == code


async def test_malformed_status_is_not_healthy():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    ) as http:
        with pytest.raises(DomainError) as exc:
            await KafkaConnectClient("http://connect", http).status("capture")
        assert exc.value.code == "CONNECT_INVALID_RESPONSE"


async def test_raw_traces_never_return_to_browser():
    runtime = {
        "connector": {"state": "RUNNING"},
        "tasks": [{"id": 0, "state": "FAILED", "trace": "credentials password=private"}],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=runtime))
    ) as http:
        result = await KafkaConnectClient("http://connect", http).status("capture")
        assert "private" not in str(result)
        assert result["tasks"][0]["state"] == "FAILED"


@pytest.mark.parametrize(
    "trace,code,retryable",
    [
        (
            "org.postgresql.util.PSQLException: FATAL SQLState: 57P01",
            "DESTINATION_DATABASE_RESTART",
            True,
        ),
        (
            "PSQLException: FATAL: terminating connection due to administrator command",
            "DESTINATION_DATABASE_RESTART",
            True,
        ),
        (
            "java.sql.SQLTransientConnectionException: Communications link failure",
            "DESTINATION_CONNECTION_UNAVAILABLE",
            True,
        ),
        (
            "password authentication failed for user private-user",
            "DESTINATION_AUTHENTICATION_FAILED",
            False,
        ),
        (
            "ConstraintViolationException: duplicate key private-row",
            "DESTINATION_CONSTRAINT_VIOLATION",
            False,
        ),
        ("unrecognized private failure", "CONNECT_TASK_FAILED", False),
    ],
)
def test_sink_task_failures_are_structured_bounded_and_redacted(trace, code, retryable):
    failure = classify_task_failure(trace)
    assert failure.code == code
    assert failure.retryable is retryable
    assert "private" not in failure.message


async def test_status_exposes_only_sanitized_recovery_signals():
    runtime = {
        "type": "sink",
        "connector": {"state": "RUNNING"},
        "tasks": [
            {
                "id": 3,
                "state": "FAILED",
                "trace": "password=private SQLState: 57P01",
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=runtime))
    ) as http:
        result = await KafkaConnectClient("http://connect", http).status("capture")
    task = result["tasks"][0]
    assert task == {
        "id": 3,
        "state": "FAILED",
        "error": "Destination database restarted or is temporarily unavailable",
        "error_code": "DESTINATION_DATABASE_RESTART",
        "error_category": "TRANSIENT_DATABASE_UNAVAILABLE",
        "retryable": True,
    }
    assert "private" not in str(result)


@pytest.mark.parametrize(
    "trace,expected",
    [
        ("requested WAL segment has already been removed password=private", "PostgreSQL"),
        ("purged binary log password=private", "MySQL"),
        ("database history topic is missing password=private", "schema history"),
        ("Offset out of range password=private", "Kafka source offset"),
    ],
)
def test_source_position_failures_are_actionable_and_redacted(trace, expected):
    hint = failure_hint(trace, "source")
    assert expected in hint
    assert "private" not in hint


async def test_source_status_maps_missing_position_without_raw_trace():
    runtime = {
        "type": "source",
        "connector": {"state": "RUNNING"},
        "tasks": [
            {
                "id": 0,
                "state": "FAILED",
                "trace": "purged binary log password=private",
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=runtime))
    ) as http:
        result = await KafkaConnectClient("http://connect", http).status("capture")
        assert "MySQL binlog position" in result["tasks"][0]["error"]
        assert "private" not in str(result)


async def test_idempotent_create_adopts_only_exact_existing_configuration():
    config = {
        "name": "capture",
        "connector.class": "example.Source",
        "database.password": "${cluecdc:reference:password}",
    }

    def exact(request):
        if request.method == "POST":
            return httpx.Response(409)
        return httpx.Response(200, json=config)

    async with httpx.AsyncClient(transport=httpx.MockTransport(exact)) as http:
        client = KafkaConnectClient("http://connect", http)
        assert not await client.create_idempotent("capture", config)
        assert (await client.config("capture"))["database.password"] == "[REDACTED]"

    def different(request):
        if request.method == "POST":
            return httpx.Response(409)
        return httpx.Response(200, json={**config, "tasks.max": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(different)) as http:
        with pytest.raises(DomainError) as exc:
            await KafkaConnectClient("http://connect", http).create_idempotent("capture", config)
        assert exc.value.code == "CONNECT_DEPLOY_CONFLICT"
