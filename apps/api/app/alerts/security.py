import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from app.core.errors import DomainError

BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}


def _public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def validate_outbound_url(url: str, *, resolve_dns: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("An HTTPS URL without embedded credentials is required")
    host = parsed.hostname.rstrip(".").lower()
    if host in BLOCKED_HOSTS or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private network destinations are not allowed")
    try:
        if not _public_ip(host):
            raise ValueError("Private network destinations are not allowed")
        return
    except ValueError as exc:
        # It was an IP and was rejected, rather than a hostname to resolve.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise exc
    if resolve_dns:
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        )
        if not records or any(not _public_ip(str(record[4][0])) for record in records):
            raise ValueError("Private network destinations are not allowed")


async def validate_channel_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    if kind == "slack":
        url = str(config.get("webhook_url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "hooks.slack.com":
            raise DomainError(
                "INVALID_CHANNEL_CONFIG", "A valid Slack HTTPS webhook URL is required", 422
            )
        return {"webhook_url": url}
    if kind == "telegram":
        token = str(config.get("bot_token") or "")
        chat_id = str(config.get("chat_id") or "")
        if not token or ":" not in token or not chat_id:
            raise DomainError(
                "INVALID_CHANNEL_CONFIG", "Telegram Bot Token and Chat ID are required", 422
            )
        return {"bot_token": token, "chat_id": chat_id}
    if kind == "webhook":
        url = str(config.get("url") or "")
        try:
            await validate_outbound_url(url)
        except ValueError as exc:
            raise DomainError("INVALID_CHANNEL_CONFIG", str(exc), 422) from None
        headers = config.get("headers") or {}
        if not isinstance(headers, dict) or len(headers) > 20:
            raise DomainError(
                "INVALID_CHANNEL_CONFIG",
                "Webhook headers must be an object with at most 20 entries",
                422,
            )
        safe_headers: dict[str, str] = {}
        for key, value in headers.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or "\n" in key + value
                or "\r" in key + value
            ):
                raise DomainError("INVALID_CHANNEL_CONFIG", "Webhook headers are invalid", 422)
            if key.lower() in {"host", "content-length", "transfer-encoding"}:
                raise DomainError(
                    "INVALID_CHANNEL_CONFIG", f"Webhook header {key} is not allowed", 422
                )
            safe_headers[key] = value
        return {
            "url": url,
            "headers": safe_headers,
            "authorization": str(config.get("authorization") or ""),
        }
    raise DomainError(
        "INVALID_CHANNEL_TYPE", "Channel type must be slack, telegram, or webhook", 422
    )


def masked_config(kind: str, configured: bool = True) -> dict[str, Any]:
    labels = {
        "slack": "https://hooks.slack.com/********",
        "telegram": "******** / ********",
        "webhook": "https://********",
    }
    return {"configured": configured, "maskedValue": labels.get(kind, "********")}
