#!/usr/bin/env python3
"""evidence_export - transform a real assertions.json into the vendor-neutral evidence.json Vanta's
and Drata's generic evidence-submission APIs both expect (item 16.3, the GRC-connectors stage).

RegScale and ServiceNow GRC both natively import NIST OSCAL, so they consume the engine's existing
``oscal-ar.json`` (``agentce.report.render_oscal``) directly -- no transform needed, see
``grc_connectors_check.py`` and ``website/src/content/docs/docs/grc-connectors.md``. Vanta and Drata
have no native OSCAL import; both instead expose a generic evidence/document/test-record API keyed on
a handful of plain fields (a control identifier, a subject, an outcome, a content digest, and a
timestamp). This module produces exactly that shape from a real ``assertions.json``
(``engines/python/agentce/assertions.py``'s ``Assertion.to_json()`` array), so a compliance team can
submit AgentCE's own findings to either vendor's evidence API without hand-transcribing a report.

**Output shape.** One ``evidence.json`` record per assertion record, sorted by the codepoint-sorted
key ``(control_id, subject)`` for a deterministic, byte-identical-across-runs order:

  * ``control_id``  the assertion's own ``control`` field, renamed. ``control_id`` is this transform's
                     own output vocabulary -- the source field is genuinely named ``control``
                     (``assertions.py``); this is a rename for a clearer external payload name, not a
                     claim that the source ever called it ``control_id``.
  * ``subject``      the assertion's own ``subject`` field, unchanged.
  * ``outcome``      the assertion's own ``outcome`` field, unchanged (one of the six values in
                      ``agentce.assertions.OUTCOMES``).
  * ``digest``       a digest of the assertion record ITSELF (not of any one evidence pointer): SHA-256
                      over the record's own JSON serialization with sorted keys and no incidental
                      whitespace (``json.dumps(record, sort_keys=True, separators=(",", ":"))``). This
                      is a simple, deterministic canonicalization chosen for this transform's own
                      digest -- not the engine's RFC 8785 profile (``agentce.canonical``), which this
                      module deliberately does not import so it stays stdlib-only. An assertion already
                      carries its own per-evidence-pointer digests under ``evidence[].digest``; this
                      field is deliberately NOT one of those (there is no single per-assertion digest
                      on the source record) -- it is a fresh digest of the whole assertion record, and
                      ``evidence_refs`` below is where the individual evidence pointers show up instead.
  * ``evidence_refs``  ``[e["ref"] for e in assertion["evidence"]]`` -- the assertion's own evidence
                        pointer references, in their original order.
  * ``checked_at``   pinned to the assertion's own ``window.end`` field, NEVER ``datetime.now()`` or
                      any other wall-clock read. ``assertions.json`` carries no other per-assertion or
                      run-level timestamp, and ``window.end`` is the real end of the evidence window
                      the assertion was computed over, so it is the honest answer to "when was this
                      checked". This is also why the determinism self-test below matters: if
                      ``checked_at`` ever read the wall clock, two runs of the same input would stop
                      being byte-identical, and that self-test would start failing nondeterministically
                      (a real regression it exists to catch, not a hypothetical one).

**Atomicity.** ``export_evidence`` fully reads and validates the input, and builds the complete output
list, before it writes a single byte of ``evidence.json``. The final write is additionally atomic (a
temp file in the destination directory, then ``os.replace``), so a failure during the write itself
(e.g. disk full) can never leave a partial or corrupt ``evidence.json`` on disk; a failure during
reading or validation raises before any output file is even opened.

    evidence_export.py --self-test                 prove determinism (real fixture, byte-identical
                                                     across two independent runs) and that malformed or
                                                     incomplete input is refused, never partially written
    evidence_export.py <assertions.json> <out.json>  the real transform

Standard library only -- no PyYAML, no ``requests``, no vendor SDK, no dependency this module's own
project (``tools/pyproject.toml``) does not already have without this file. No network, no credential,
no learned component.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

#: The fields transform_assertion refuses to proceed without (beyond "is a JSON object" itself).
REQUIRED_FIELDS = ("control", "subject", "outcome", "window")

#: The real output field names this module writes -- also the vocabulary
#: ``grc_connectors_check.py`` cross-checks against ``grc-connectors.md``.
OUTPUT_FIELDS = (
    "control_id",
    "subject",
    "outcome",
    "digest",
    "evidence_refs",
    "checked_at",
)


class EvidenceExportError(Exception):
    """A named, stable refusal (never a raw traceback) for malformed or incomplete input.

    ``key`` is a stable message key, mirroring the rest of this repository's error convention
    (``AgentceError`` in ``engines/python/agentce/errors.py``), even though this module does not
    import that class so it can stay dependency-free of the engine package.
    """

    def __init__(self, key: str, message: str) -> None:
        self.key = key
        super().__init__(message)


def load_assertions(path: Path) -> list[Any]:
    """Read and JSON-parse ``path``; refuse (never a raw traceback) if it cannot be read, is not valid
    JSON, or is not a JSON array."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceExportError(
            "evidence_export.input_not_found", f"cannot read {path}: {exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceExportError(
            "evidence_export.invalid_json", f"{path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise EvidenceExportError(
            "evidence_export.not_a_list",
            f"{path} must be a JSON array of assertion records, got {type(data).__name__}",
        )
    return data


def _require_fields(record: Any, index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise EvidenceExportError(
            "evidence_export.record_not_object",
            f"assertion[{index}] is not a JSON object",
        )
    missing = [name for name in REQUIRED_FIELDS if name not in record]
    if missing:
        raise EvidenceExportError(
            "evidence_export.missing_field",
            f"assertion[{index}] is missing required field(s): {', '.join(missing)}",
        )
    window = record.get("window")
    if not isinstance(window, dict) or "end" not in window:
        raise EvidenceExportError(
            "evidence_export.missing_field",
            f"assertion[{index}] window is missing required field 'end'",
        )
    return record


def digest_of(record: dict[str, Any]) -> str:
    """SHA-256 over the assertion record's own sorted-key, no-whitespace JSON serialization -- a
    digest OF the assertion record itself, not of any one evidence pointer (see module docstring)."""
    canonical = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def transform_assertion(record: Any, index: int = 0) -> dict[str, Any]:
    """Transform one real assertion record into one ``evidence.json`` record (see module docstring
    for the field-by-field rename/derivation). Raises :class:`EvidenceExportError` on malformed or
    incomplete input; never returns a partial record."""
    validated = _require_fields(record, index)
    evidence = validated.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    refs = [e["ref"] for e in evidence if isinstance(e, dict) and "ref" in e]
    return {
        "control_id": validated["control"],
        "subject": validated["subject"],
        "outcome": validated["outcome"],
        "digest": digest_of(validated),
        "evidence_refs": refs,
        "checked_at": validated["window"]["end"],
    }


def export_records(assertions: list[Any]) -> list[dict[str, Any]]:
    """Transform every assertion record and sort by the codepoint-sorted ``(control_id, subject)``
    key. Raises before returning anything if any one record is malformed (no partial result)."""
    records = [transform_assertion(a, i) for i, a in enumerate(assertions)]
    records.sort(key=lambda r: (r["control_id"], r["subject"]))
    return records


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: a temp file in the same directory, then ``os.replace``,
    so a failure mid-write can never leave a partial or corrupt file at ``path``."""
    directory = path.parent if str(path.parent) else Path(".")
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise


def export_evidence(
    assertions_path: str | Path, out_path: str | Path
) -> list[dict[str, Any]]:
    """Read ``assertions_path``, transform every record, and atomically write ``out_path``. Fully
    validates and builds the complete output before opening ``out_path`` for writing at all, so a
    malformed or incomplete input never produces a partial ``evidence.json`` (self-tested below)."""
    data = load_assertions(Path(assertions_path))
    records = export_records(data)
    text = json.dumps(records, indent=2, sort_keys=False) + "\n"
    _atomic_write(Path(out_path), text)
    return records


# --- self-test ---------------------------------------------------------------------------------------


def _self_test_determinism() -> bool:
    fixture = ROOT / "spec" / "report" / "examples" / "assertions.example.json"
    if not fixture.is_file():
        print(f"self-test determinism: FAIL ({fixture} is missing)")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out1 = Path(tmp) / "evidence-1.json"
        out2 = Path(tmp) / "evidence-2.json"
        export_evidence(fixture, out1)
        export_evidence(fixture, out2)
        bytes1, bytes2 = out1.read_bytes(), out2.read_bytes()
    if bytes1 != bytes2:
        print(
            "self-test determinism-byte-identical: FAIL (two runs of the real fixture diverged)"
        )
        return False
    records = json.loads(bytes1)
    if not records or set(records[0]) != set(OUTPUT_FIELDS):
        print(
            f"self-test determinism-byte-identical: FAIL (unexpected fields {sorted(records[0]) if records else records})"
        )
        return False
    print("self-test determinism-byte-identical: ok")
    return True


def _self_test_refuses_malformed() -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        # Malformed JSON entirely.
        bad_json = Path(tmp) / "bad-json.json"
        bad_json.write_text("{not valid json", encoding="utf-8")
        out = Path(tmp) / "out-bad-json.json"
        try:
            export_evidence(bad_json, out)
            print("self-test refuses-malformed-json: FAIL (did not raise)")
            ok = False
        except EvidenceExportError as exc:
            if out.exists():
                print(
                    "self-test refuses-malformed-json: FAIL (a partial file was written)"
                )
                ok = False
            elif exc.key != "evidence_export.invalid_json":
                print(f"self-test refuses-malformed-json: FAIL (wrong key {exc.key!r})")
                ok = False
            else:
                print("self-test refuses-malformed-json: ok")

        # Valid JSON, but missing a required field.
        missing_field = Path(tmp) / "missing-field.json"
        missing_field.write_text(
            json.dumps(
                [{"control": "OVS-03", "subject": "x", "outcome": "conformant"}]
            ),
            encoding="utf-8",
        )
        out2 = Path(tmp) / "out-missing-field.json"
        try:
            export_evidence(missing_field, out2)
            print("self-test refuses-missing-field: FAIL (did not raise)")
            ok = False
        except EvidenceExportError as exc:
            if out2.exists():
                print(
                    "self-test refuses-missing-field: FAIL (a partial file was written)"
                )
                ok = False
            elif exc.key != "evidence_export.missing_field":
                print(f"self-test refuses-missing-field: FAIL (wrong key {exc.key!r})")
                ok = False
            else:
                print("self-test refuses-missing-field: ok")

        # A pre-existing out.json at the destination is left untouched by a refused run.
        untouched = Path(tmp) / "out-untouched.json"
        untouched.write_text('{"already": "here"}', encoding="utf-8")
        try:
            export_evidence(bad_json, untouched)
        except EvidenceExportError:
            pass
        if untouched.read_text(encoding="utf-8") != '{"already": "here"}':
            print("self-test refused-run-does-not-clobber-existing-output: FAIL")
            ok = False
        else:
            print("self-test refused-run-does-not-clobber-existing-output: ok")
    return ok


def self_test() -> int:
    ok = _self_test_determinism() and _self_test_refuses_malformed()
    print(f"evidence_export self-test: {'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    if len(args) == 2:
        try:
            records = export_evidence(args[0], args[1])
        except EvidenceExportError as exc:
            print(f"FAIL [{exc.key}] {exc}", file=sys.stderr)
            return 1
        print(f"evidence_export: wrote {len(records)} record(s) to {args[1]}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
