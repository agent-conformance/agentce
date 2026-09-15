"""Structured logging: JSON records, level selection, and idempotent configuration."""

from __future__ import annotations

import json
import logging

from agentce import logsetup


def test_configure_sets_level() -> None:
    assert logsetup.configure(debug=True).level == logging.DEBUG
    assert logsetup.configure(quiet=True).level == logging.WARNING
    assert logsetup.configure().level == logging.INFO


def test_configure_is_idempotent() -> None:
    first = logsetup.configure()
    second = logsetup.configure(debug=True)
    assert first is second
    assert len(second.handlers) == 1
    assert second.propagate is False


def test_json_formatter_single_line_object() -> None:
    formatter = logsetup.JsonFormatter()
    record = logging.LogRecord(
        "agentce", logging.INFO, __file__, 1, "command.start", None, None
    )
    record.command = "version"
    line = formatter.format(record)
    assert "\n" not in line
    obj = json.loads(line)
    assert obj["event"] == "command.start"
    assert obj["level"] == "info"
    assert obj["logger"] == "agentce"
    assert obj["command"] == "version"
    assert obj["ts"].endswith("+00:00")


def test_json_formatter_includes_exception() -> None:
    formatter = logsetup.JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "agentce", logging.ERROR, __file__, 1, "oops", None, sys.exc_info()
        )
    obj = json.loads(formatter.format(record))
    assert "ValueError" in obj["exc"]


def test_get_logger_name() -> None:
    assert logsetup.get_logger().name == "agentce"
