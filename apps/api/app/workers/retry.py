import random
from dataclasses import dataclass

from app.core.errors import DomainError


@dataclass(frozen=True)
class FailureDecision:
    code: str
    category: str
    retryable: bool


PERMANENT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("WAL", "BINLOG", "OFFSET", "REPLICATION_SLOT", "SOURCE_HISTORY"), "MISSING_SOURCE_HISTORY"),
    (("AUTH", "AUTHORIZATION", "PERMISSION", "FORBIDDEN"), "AUTHENTICATION"),
    (("SCHEMA", "UNSUPPORTED_TYPE", "DESTINATION_WRITE"), "SCHEMA_INCOMPATIBILITY"),
    (("INVALID", "VALIDATION", "UNKNOWN_JOB", "NOT_FOUND", "ALREADY_"), "INVALID_CONFIGURATION"),
)
TRANSIENT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("RESOURCE_EXHAUSTED", "RATE_LIMITED", "THROTTLED", "TOO_MANY", "BUSY"),
        "RESOURCE_EXHAUSTION",
    ),
    (
        (
            "UNAVAILABLE",
            "TIMEOUT",
            "TIMED_OUT",
            "CONNECTION",
            "CONNECT_REQUEST_FAILED",
            "LEASE_EXPIRED",
        ),
        "TRANSIENT_CONNECTIVITY",
    ),
)


def classify_failure(exc: Exception) -> FailureDecision:
    """Classify retry safety without persisting raw exception text."""
    if isinstance(exc, DomainError):
        code = exc.code.upper()
        for needles, category in PERMANENT_RULES:
            if any(needle in code for needle in needles):
                return FailureDecision(code, category, False)
        for needles, category in TRANSIENT_RULES:
            if any(needle in code for needle in needles):
                return FailureDecision(code, category, True)
        # Unknown domain failures remain bounded by JOB_MAX_ATTEMPTS.
        return FailureDecision(code, "UNKNOWN", True)
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return FailureDecision(type(exc).__name__.upper(), "TRANSIENT_CONNECTIVITY", True)
    return FailureDecision("UNEXPECTED_ERROR", "UNKNOWN", True)


def retry_delay_seconds(
    attempt: int,
    base_seconds: float,
    maximum_seconds: float,
    jitter_ratio: float,
) -> float:
    """Return capped exponential delay with positive jitter."""
    exponential = min(maximum_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0, exponential * jitter_ratio)
    return min(maximum_seconds, exponential + jitter)
