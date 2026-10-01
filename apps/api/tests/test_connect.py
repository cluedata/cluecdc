import httpx
import pytest

from app.adapters.connect import KafkaConnectClient
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
