"""Structured logging for the CLI (SPEC §13.4 AX-8: a ``--json`` form of everything).

Logs are line-delimited JSON on **stderr**, so that stdout carries only the command's own output and
automation can consume both cleanly. The default level is ``info``; ``--quiet`` raises it to
``warning`` and ``--debug`` lowers it to ``debug``. Only ``--debug`` lets a stack trace reach the
user (AX-6). Timestamps use RFC 3339 UTC; they appear in logs only, never in evaluation outputs, so
they do not affect determinism.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "agentce"

_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object on a single line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure(*, debug: bool = False, quiet: bool = False) -> logging.Logger:
    """Configure and return the ``agentce`` logger; safe to call more than once."""
    level = logging.DEBUG if debug else (logging.WARNING if quiet else logging.INFO)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    # Replace any existing handler with a fresh one bound to the current stderr, so the logger never
    # writes to a stream captured by an earlier call (which under a test harness is closed and would
    # raise a logging error), and so repeated calls never accumulate duplicate handlers.
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler: logging.Handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    """Return the ``agentce`` logger without reconfiguring it."""
    return logging.getLogger(LOGGER_NAME)
