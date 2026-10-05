"""Quarantine records match quarantine.schema.json and serialise deterministically."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from agentce.errors import InputError
from agentce.quarantine import (
    QuarantineReason,
    QuarantineRecord,
    counts_by_reason,
    write_quarantine,
)

from conftest import unwritable_dir

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUARANTINE_SCHEMA = json.loads(
    (_REPO_ROOT / "spec" / "report" / "quarantine.schema.json").read_text(
        encoding="utf-8"
    )
)


def test_record_to_json_minimal() -> None:
    assert QuarantineRecord(QuarantineReason.SCHEMA_INVALID).to_json() == {
        "reason": "schema_invalid"
    }


def test_record_validates_against_schema() -> None:
    record = QuarantineRecord(
        QuarantineReason.UNKNOWN_TYPE,
        event_id="e",
        source="s",
        stream="st",
        type="t",
        detail="d",
    )
    jsonschema.validate(record.to_json(), _QUARANTINE_SCHEMA)


def test_reasons_match_schema_enum() -> None:
    allowed = set(_QUARANTINE_SCHEMA["properties"]["reason"]["enum"])
    assert {reason.value for reason in QuarantineReason} == allowed


def test_counts_by_reason_is_sorted() -> None:
    records = [
        QuarantineRecord(QuarantineReason.SCHEMA_INVALID),
        QuarantineRecord(QuarantineReason.SCHEMA_INVALID),
        QuarantineRecord(QuarantineReason.OVERSIZE),
    ]
    assert counts_by_reason(records) == {"oversize": 1, "schema_invalid": 2}


def test_write_quarantine(tmp_path: Path) -> None:
    records = [QuarantineRecord(QuarantineReason.DUPLICATE_ID, event_id="x")]
    path = tmp_path / "out" / "quarantine.jsonl"
    assert write_quarantine(records, path) == 1
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"event_id": "x", "reason": "duplicate_id"}


def test_write_quarantine_raises_a_keyed_error_on_an_unwritable_out_dir(
    tmp_path: Path,
) -> None:
    with unwritable_dir(tmp_path / "ro") as ro:
        with pytest.raises(InputError) as excinfo:
            write_quarantine([], ro / "out" / "quarantine.jsonl")
    assert excinfo.value.key == "input.out_dir_unwritable"


def test_write_quarantine_raises_a_keyed_error_on_a_pre_existing_unwritable_out_dir(
    tmp_path: Path,
) -> None:
    """A directory that already exists but lost its write bit after creation must raise the same
    keyed error on the actual write, not only on a failed `mkdir` (SPEC: matches every other
    input-path failure's pattern, never the internal.unexpected catch-all)."""
    with unwritable_dir(tmp_path / "out") as out:
        with pytest.raises(InputError) as excinfo:
            write_quarantine([], out / "quarantine.jsonl")
    assert excinfo.value.key == "input.out_dir_unwritable"
