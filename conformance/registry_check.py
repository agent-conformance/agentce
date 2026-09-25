"""The implementation-report registry gate (SPEC §11.5, §14.5 CP-1, P5.6).

An implementation report is accepted into ``conformance/reports/`` and *listed* as a conforming engine
or adapter only if the golden revision it names verifies against its signature and the report claims
``full`` with ``no_ml: pass``. A report whose golden signature is missing, does not verify, or attests
a different digest than the report names is ``unverified`` and is not listed (SPEC §14.5 CP-1, CP-4).

``registry_check --self-test`` proves the check itself: a well-formed report is accepted, and a report
whose golden digest does not match its signature is refused. It prints ``REGISTRY SELF-TEST PASSED``.

Run it as::

    cd conformance && uv run python registry_check.py --self-test
    cd conformance && uv run python registry_check.py --json      # verify the committed registry
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import jsonschema

import dev_trust
from agentce import signing

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "conformance" / "reports"
SCHEMA_FILE = (
    REPO_ROOT / "conformance" / "registry" / "implementation-report.schema.json"
)
GOLDEN_PREDICATE = "https://agent-conformance.org/attestation/golden/v1"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _attested_digest(verified: signing.Verified) -> str | None:
    """The subject digest the verified attestation actually covers."""
    statement = json.loads(verified.payload)
    subjects = statement.get("subject") or []
    if not subjects:
        return None
    for algo, hexval in (subjects[0].get("digest") or {}).items():
        return f"{algo}:{hexval}"
    return None


def evaluate_report(
    record: dict[str, Any],
    trust: signing.TrustRoot,
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether one implementation report is listed, and why not when it is not.

    A schema-validation step runs first (P16.8): a structurally malformed record -- missing a
    schema-required field such as ``claim`` -- is refused with a distinct ``schema-invalid`` status
    before signature verification is even attempted, never reaching the golden-signature branch.
    """
    schema = schema if schema is not None else load_schema()
    try:
        jsonschema.validate(instance=record, schema=schema)
    except jsonschema.ValidationError as exc:
        return {
            "engine": record.get("engine"),
            "status": "schema-invalid",
            "listed": False,
            "reasons": [f"schema validation failed: {exc.message}"],
        }

    reasons: list[str] = []
    golden = record.get("golden")
    golden = golden if isinstance(golden, dict) else {}
    envelope = golden.get("signature")
    if not isinstance(envelope, dict):
        reasons.append("golden signature is missing")
    else:
        try:
            verified = signing.verify_envelope(envelope, trust)
        except signing.VerificationError as exc:
            reasons.append(f"golden signature does not verify: {exc}")
        else:
            attested = _attested_digest(verified)
            if attested != golden.get("digest"):
                reasons.append("golden digest does not match the signed attestation")
    verified_ok = not reasons
    claim = record.get("claim")
    no_ml = record.get("no_ml")
    listed = verified_ok and claim == "full" and no_ml == "pass"
    if verified_ok and not listed:
        reasons.append(f"not conforming (claim={claim!r}, no_ml={no_ml!r})")
    status = (
        "listed" if listed else ("unverified" if not verified_ok else "not-conforming")
    )
    return {
        "engine": record.get("engine"),
        "status": status,
        "listed": listed,
        "reasons": reasons,
    }


def load_reports(reports_dir: Path) -> list[dict[str, Any]]:
    """Every report JSON in ``reports_dir`` (the README is not a report)."""
    records: list[dict[str, Any]] = []
    for path in sorted(reports_dir.glob("*.json")):
        records.append(json.loads(path.read_text("utf-8")))
    return records


def run(reports_dir: Path) -> dict[str, Any]:
    """Verify every committed report and return the listing."""
    trust = signing.vendored_trust()
    results = [evaluate_report(record, trust) for record in load_reports(reports_dir)]
    return {
        "reports": len(results),
        "listed": [r for r in results if r["listed"]],
        "rejected": [r for r in results if not r["listed"]],
    }


def _signed_golden(digest: str) -> dict[str, Any]:
    """Sign a golden-revision attestation over ``digest`` with the development release key."""
    statement = signing.intoto_statement(
        "golden", digest, GOLDEN_PREDICATE, {"kind": "golden"}
    )
    return signing.sign_statement(
        statement, signing.KmsSigner(private_key=dev_trust.catalog_key())
    )


def _sample_report(golden_digest: str) -> dict[str, Any]:
    return {
        "engine": {
            "impl": "agentce",
            "version": "0.0.0",
            "package_digest": "sha256:" + "a" * 64,
        },
        "claim": "full",
        "no_ml": "pass",
        "golden": {
            "revision": "self-test",
            "digest": golden_digest,
            "signature": _signed_golden(golden_digest),
        },
    }


def self_test() -> int:
    """Accept a well-formed report; refuse one whose golden digest does not verify."""
    trust = signing.vendored_trust()
    good_digest = "sha256:" + "0" * 64
    good = evaluate_report(_sample_report(good_digest), trust)
    if not good["listed"]:
        print(
            f"REGISTRY SELF-TEST FAILED: a well-formed report was refused: {good['reasons']}"
        )
        return 1

    tampered = _sample_report(good_digest)
    tampered["golden"]["digest"] = (
        "sha256:" + "1" * 64
    )  # claim a golden the signature does not cover
    refused = evaluate_report(tampered, trust)
    if refused["listed"] or refused["status"] != "unverified":
        print(
            "REGISTRY SELF-TEST FAILED: a report with an unverifiable golden digest was listed"
        )
        return 1

    malformed = _sample_report(good_digest)
    del malformed["claim"]
    schema_invalid = evaluate_report(malformed, trust)
    if schema_invalid["listed"] or schema_invalid["status"] != "schema-invalid":
        print(
            "REGISTRY SELF-TEST FAILED: a report missing its schema-required 'claim' field "
            f"was not refused as schema-invalid (got status={schema_invalid['status']!r})"
        )
        return 1

    print("REGISTRY SELF-TEST PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="registry_check",
        description="Implementation-report registry gate (SPEC §14.5 CP-1).",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="prove the check and exit"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    summary = run(REPORTS_DIR)
    if args.json:
        print(
            json.dumps(
                {"reports": summary["reports"], "listed": len(summary["listed"])},
                sort_keys=True,
            )
        )
    else:
        print(f"reports={summary['reports']} listed={len(summary['listed'])}")
        for rejected in summary["rejected"]:
            print(
                f"  rejected {rejected['engine']}: {rejected['status']}: {'; '.join(rejected['reasons'])}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
