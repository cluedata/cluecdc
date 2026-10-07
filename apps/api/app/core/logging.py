import logging

import structlog

from app.core.config import get_settings
from app.core.errors import redact


def configure_logging(service: str) -> None:
    # Uvicorn's raw access line would include single-use invite tokens from the
    # URL. The structured HTTP middleware replaces it and redacts that segment.
    logging.getLogger("uvicorn.access").disabled = True

    def service_context(_, __, event):
        event.setdefault("service", service)
        return redact(event)

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, get_settings().log_level.upper())
        ),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            service_context,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
    )
