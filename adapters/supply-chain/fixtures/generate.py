"""Regenerate the signed attestation fixtures deterministically (SPEC 12.3).

``attestations/`` and ``attestations-hostile/`` are built here from published test seeds (they sign
fixtures only and protect nothing) using Ed25519, which is deterministic, so a second run is
byte-identical. The pre-authentication encoding is written out again below rather than imported from the
adapter, and each hostile case carries the status it must produce, asserted before anything is written.
``attestations-interop/`` is signed by Node (``sign.mjs``); only its expected events are rebuilt here.

    uv run python fixtures/generate.py           # rewrite the fixtures
    uv run python fixtures/generate.py --check   # fail if a committed file differs
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from agentce_adapters import adapt
from agentce_adapters.fixtures import load_fixture

HERE = Path(__file__).resolve().parent
INTOTO = "application/vnd.in-toto+json"
SUBJECT = "spiffe://corp/agents/credit-underwriter"
AGENT = {"id": SUBJECT, "name": "credit-underwriter"}

SEEDS = {"key-fulcio-1": 0xA1, "key-a": 0xA1, "key-b": 0xB2, "key-rogue": 0xEE}
KEYS = {
    name: ed25519.Ed25519PrivateKey.from_private_bytes(bytes([n]) * 32)
    for name, n in SEEDS.items()
}
P256_PUBLIC_PEM = (
    ec.derive_private_key(0x1234567, ec.SECP256R1())
    .public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode("ascii")
)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def raw_public(name: str) -> str:
    return b64(
        KEYS[name]
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )


def pae(payload_type: str, payload: bytes) -> bytes:
    kind = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(payload), payload)


def statement(*digests: str) -> dict[str, Any]:
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {"name": f"artifact-{i}", "digest": {"sha256": d}}
            for i, d in enumerate(digests)
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {},
    }


def envelope(
    signer: str, keyid: str, stmt: dict[str, Any], payload_type: str = INTOTO
) -> dict[str, Any]:
    payload = json.dumps(stmt, sort_keys=True).encode("utf-8")
    signature = KEYS[signer].sign(pae(payload_type, payload))
    return {
        "payloadType": payload_type,
        "payload": b64(payload),
        "signatures": [{"keyid": keyid, "sig": b64(signature)}],
    }


def record(
    record_id: str,
    second: int,
    fmt: str,
    version: str,
    env: dict[str, Any] | None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "timestamp": f"2026-05-08T09:00:{second:02d}.000Z",
        "id": record_id,
        "kind": "attestation",
        "format": fmt,
        "convention_version": version,
        "system": "admission-controller",
    }
    if env is not None:
        out["dsse"] = env
    out.update(extra)
    return out


def attestations() -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    base = statement("aaaa1111", "bbbb2222")
    rows = [
        record(
            "att-1",
            0,
            "sigstore",
            "0.3",
            envelope("key-fulcio-1", "key-fulcio-1", base),
            trace_id="5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c",
            span_id="1111111111111111",
            session_id="sess-sc",
            agent=AGENT,
            observed_digests=["sha256:aaaa1111", "sha256:bbbb2222"],
        ),
        record(
            "att-2",
            1,
            "in-toto",
            "1.0",
            envelope("key-fulcio-1", "key-fulcio-1", statement("cccc3333")),
            observed_digests=["sha256:dddd4444"],
        ),
        record(
            "att-3",
            2,
            "in-toto",
            "1.0",
            None,
            statement_type=base["_type"],
            subject_digests=["sha256:eeee5555"],
        ),
        record(
            "att-4",
            3,
            "sigstore",
            "0.3",
            envelope("key-rogue", "key-rogue", statement("ffff6666")),
            observed_digests=["sha256:ffff6666"],
        ),
    ]
    adapt_args = {
        "subject": SUBJECT,
        "source_class": "enforcement_point",
        "trusted_keys": {"key-fulcio-1": raw_public("key-fulcio-1")},
    }
    return rows, adapt_args, ["verified", "failed", "unverified", "failed"]


def hostile() -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    good = statement("aaaa1111")
    observed = {"observed_digests": ["sha256:aaaa1111"]}
    swapped = envelope("key-a", "key-a", good)
    swapped["payload"] = b64(
        json.dumps(statement("bbbb2222"), sort_keys=True).encode("utf-8")
    )
    forged = envelope("key-a", "key-a", good)
    forged["signatures"][0]["sig"] = b64(bytes(64))
    keyless_mix = envelope("key-rogue", "key-rogue", good)
    keyless_mix["signatures"] += [
        {"sig": b64(bytes(64))},
        {"keyid": "key-nomaterial", "sig": b64(bytes(64))},
    ]
    bad_payload = envelope("key-a", "key-a", good)
    bad_payload["payload"] = "!!not base64!!"
    empty = envelope("key-a", "key-a", good)
    empty["signatures"] = []
    legacy = record(
        "hostile-12",
        12,
        "in-toto",
        "1.0",
        None,
        signature={"key_id": "key-a"},
        subject_digests=["sha256:aaaa1111"],
        **observed,
    )
    unobserved = record(
        "hostile-11", 11, "in-toto", "1.0", envelope("key-a", "key-a", good)
    )

    cases: list[tuple[dict[str, Any], str]] = [
        (
            record(
                "hostile-1",
                1,
                "in-toto",
                "1.0",
                envelope("key-a", "key-a", good),
                **observed,
            ),
            "verified",
        ),  # control
        (
            record("hostile-2", 2, "in-toto", "1.0", forged, **observed),
            "failed",
        ),  # forged signature, trusted key id
        (
            record(
                "hostile-3",
                3,
                "in-toto",
                "1.0",
                envelope("key-rogue", "key-rogue", good),
                **observed,
            ),
            "failed",
        ),  # untrusted signer
        (
            record(
                "hostile-4",
                4,
                "in-toto",
                "1.0",
                swapped,
                observed_digests=["sha256:bbbb2222"],
            ),
            "failed",
        ),  # payload swapped
        (
            record(
                "hostile-5",
                5,
                "in-toto",
                "1.0",
                envelope("key-a", "key-a", good, "text/plain"),
                **observed,
            ),
            "failed",
        ),  # wrong type
        (
            record(
                "hostile-6",
                6,
                "in-toto",
                "1.0",
                envelope("key-a", "key-b", good),
                **observed,
            ),
            "failed",
        ),  # keyid relabelled
        (
            record(
                "hostile-7",
                7,
                "in-toto",
                "1.0",
                envelope("key-a", "key-p256", good),
                **observed,
            ),
            "failed",
        ),  # key-type mismatch
        (
            record(
                "hostile-8",
                8,
                "in-toto",
                "1.0",
                envelope("key-a", "key-nomaterial", good),
                **observed,
            ),
            "unverified",
        ),
        (
            record("hostile-9", 9, "in-toto", "1.0", keyless_mix, **observed),
            "failed",
        ),  # no laundering by extra entries
        (
            record(
                "hostile-10",
                10,
                "in-toto",
                "1.0",
                envelope("key-a", "key-a", good),
                observed_digests=["sha256:cccc3333"],
            ),
            "failed",
        ),
        (unobserved, "unverified"),  # nothing observed to bind the statement to
        (legacy, "unverified"),  # a key id is not a signature
        (record("hostile-13", 13, "in-toto", "1.0", bad_payload, **observed), "failed"),
        (record("hostile-14", 14, "in-toto", "1.0", empty, **observed), "unverified"),
        (
            record(
                "hostile-15",
                15,
                "in-toto",
                "1.0",
                envelope("key-a", "key-a", good),
                signer="CN=release-manager",
                log_ref="rekor://index/1",
                method="sigstore-bundle",
                statement_type="https://example.com/forged",
                subject_digests=["sha256:forged"],
                **observed,
            ),
            "verified",  # the forged unsigned fields must not surface (asserted by test_signatures)
        ),
    ]
    adapt_args = {
        "subject": SUBJECT,
        "source_class": "enforcement_point",
        "trusted_keys": {
            "key-a": raw_public("key-a"),
            "key-b": raw_public("key-b"),
            "key-p256": P256_PUBLIC_PEM,
            "key-nomaterial": None,
        },
    }
    return [c for c, _ in cases], adapt_args, [status for _, status in cases]


def render(
    rows: list[dict[str, Any]], adapt_args: dict[str, Any], expected_status: list[str]
) -> dict[str, str]:
    lines = [json.dumps(row, separators=(",", ":")) for row in rows]
    input_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    events = adapt(input_bytes, **adapt_args).events
    if len(events) != len(expected_status):
        raise SystemExit(
            f"expected {len(expected_status)} events, adapter emitted {len(events)}"
        )
    for event, wanted in zip(events, expected_status, strict=True):
        got = event["data"]["verification"]["status"]
        if got != wanted:
            raise SystemExit(f"{event['id']}: expected {wanted}, adapter said {got}")
    return {
        "input.jsonl": input_bytes.decode("utf-8"),
        "adapt.json": json.dumps(adapt_args, indent=2) + "\n",
        "expected.jsonl": "".join(json.dumps(event) + "\n" for event in events),
    }


def interop_expected() -> dict[str, str]:
    events = load_fixture(HERE / "attestations-interop").run().events
    statuses = [event["data"]["verification"]["status"] for event in events]
    if statuses != ["verified"]:
        raise SystemExit(f"interop envelope did not verify: {statuses}")
    return {"expected.jsonl": "".join(json.dumps(event) + "\n" for event in events)}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    outputs = {
        "attestations": render(*attestations()),
        "attestations-hostile": render(*hostile()),
        "attestations-interop": interop_expected(),
    }
    stale = []
    for name, files in outputs.items():
        for filename, text in files.items():
            path = HERE / name / filename
            if check:
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    stale.append(f"{name}/{filename}")
            else:
                path.parent.mkdir(exist_ok=True)
                path.write_text(text, encoding="utf-8")
    if stale:
        print("fixtures out of date: " + ", ".join(stale), file=sys.stderr)
        return 1
    print("fixtures ok" if check else "fixtures written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
