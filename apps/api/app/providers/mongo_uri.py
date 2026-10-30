"""Validate without ever including a credential-bearing URI in an exception."""

from urllib.parse import parse_qsl, urlsplit


def validate_uri(uri: str, tls: bool = True) -> str:
    try:
        parsed = urlsplit(uri)
        if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.netloc or parsed.fragment:
            raise ValueError
        options = {key.lower(): value.lower() for key, value in parse_qsl(parsed.query)}
        insecure = {"tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"}
        if any(options.get(key) == "true" for key in insecure):
            raise ValueError
        if tls and any(options.get(key) == "false" for key in {"tls", "ssl"}):
            raise ValueError
        if options.get("readpreference", "primary") not in {"primary", "primarypreferred"}:
            raise ValueError
        if any(ord(char) < 32 for char in uri):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Invalid MongoDB URI, TLS verification, or read preference settings") from None
    return uri
