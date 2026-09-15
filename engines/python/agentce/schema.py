"""Access to the evidence JSON Schema generated from the LinkML model (SPEC §6, item 0.3).

The engine vendors a copy of ``spec/model/generated/json-schema/agentce-evidence.schema.json`` under
``data/`` so it can validate events when installed; a test keeps the copy byte-identical to the
generated artifact. Events are validated against the ``EvidenceEvent`` envelope definition, and the
``EventType`` enum is read from the same schema so the accepted event types never drift from the
model.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

import jsonschema
from jsonschema.protocols import Validator


@functools.lru_cache(maxsize=1)
def evidence_schema() -> dict[str, Any]:
    """Return the parsed vendored evidence schema."""
    text = (
        resources.files("agentce.data")
        .joinpath("agentce-evidence.schema.json")
        .read_text(encoding="utf-8")
    )
    parsed: dict[str, Any] = json.loads(text)
    return parsed


@functools.lru_cache(maxsize=1)
def event_types() -> frozenset[str]:
    """Return the set of valid ``EventType`` names (SPEC §6.2.3)."""
    enum = evidence_schema()["$defs"]["EventType"]["enum"]
    return frozenset(str(name) for name in enum)


@functools.lru_cache(maxsize=None)
def _validator_for_def(ref: str) -> Validator:
    """Return a cached validator for the ``#/$defs/<ref>`` definition of the evidence schema."""
    full = evidence_schema()
    sub_schema: dict[str, Any] = {
        "$schema": full.get("$schema"),
        "$defs": full["$defs"],
        "$ref": f"#/$defs/{ref}",
    }
    validator_cls = jsonschema.validators.validator_for(full)
    validator_cls.check_schema(sub_schema)
    return validator_cls(sub_schema)


def validate_event(event: Any) -> list[str]:
    """Return schema-validation error messages for ``event`` (empty when it is valid).

    Events are validated in two steps, mirroring the model's structure (item 0.3): the CloudEvents
    envelope is validated against ``EvidenceEvent`` with ``data`` emptied (its range is the generic
    ``Payload``), and the JSON-LD payload is validated against the specific ``<@type>Payload``
    definition named by ``data.@type``.
    """
    if not isinstance(event, dict):
        return ["event is not a JSON object"]

    errors = [
        e.message
        for e in _validator_for_def("EvidenceEvent").iter_errors({**event, "data": {}})
    ]

    data = event.get("data")
    if not isinstance(data, dict):
        errors.append("data is missing or not a JSON object")
        return errors
    payload_type = data.get("@type")
    if not isinstance(payload_type, str):
        errors.append("data.@type is missing or not a string")
        return errors
    definition = f"{payload_type}Payload"
    if definition not in evidence_schema()["$defs"]:
        errors.append(f"unknown payload type {payload_type!r}")
        return errors
    payload = {k: v for k, v in data.items() if k not in ("@context", "@type")}
    errors.extend(
        e.message for e in _validator_for_def(definition).iter_errors(payload)
    )
    return errors
