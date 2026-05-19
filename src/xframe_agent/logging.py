"""Structured logging setup and secret redaction."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

import structlog
from structlog.typing import EventDict, WrappedLogger

from xframe_agent.settings import Settings

SECRET_KEY_PATTERN = re.compile(
    r"(secret|token|password|api[_-]?key|authorization|cookie)", re.IGNORECASE
)
BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return BEARER_PATTERN.sub("Bearer <redacted>", value)
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if SECRET_KEY_PATTERN.search(str(key)) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def redact_secrets(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor that removes secrets from log events."""

    return {
        key: "<redacted>" if SECRET_KEY_PATTERN.search(key) else _redact_value(value)
        for key, value in event_dict.items()
    }


def setup_logging(settings: Settings) -> None:
    """Configure stdlib logging and structlog."""

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
