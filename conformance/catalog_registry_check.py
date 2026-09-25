"""The catalog registry gate (item 16.4, P16.8, "catalog registry (technical parts)").

`conformance/registry/catalogs.json` lists one entry per published, signed AgentCE base catalog
(`id`, `version`, `digest`, `source_path`, signing metadata), validated against
`conformance/registry/catalog-registry.schema.json`. This is the catalog-registry half of item 16.4's
two registries; `registry_check.py` is the separate, pre-existing implementation-report registry
(SPEC §11.5, §14.5 CP-1) and has no catalog concept at all.

An entry is ``listed`` only if both hold: its recorded ``digest`` matches a fresh recomputation of
`source_path` on disk (the same `signing.digest_tree` primitive `conformance/offline_bundle.py`
already uses), and the catalog directory's detached signature verifies against the vendored trust
root (`signing.verify_catalog_directory`, the same primitive the engine's own `agentce verify
--catalog` uses). A rebranded or edited catalog under the same id -- whose digest no longer matches --
is refused, never silently listed (the same guardrail the implementation-report registry already
applies to reports).

The schema lives under `conformance/registry/`, not `spec/report/`: the latter is SPEC §9's published
report-format surface, checked on every pull request by `docs/build.py` and the `website` CI job via
`website/iri-manifest.json`; the catalog registry is not yet a public, served artifact (see
`docs/adr/0020-catalog-registry.md`), so it does not belong on that surface yet.

Run it as::

    cd conformance && uv run python catalog_registry_check.py --self-test
    cd conformance && uv run python catalog_registry_check.py
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

from agentce import signing

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = REPO_ROOT / "conformance" / "registry"
REGISTRY_FILE = REGISTRY_DIR / "catalogs.json"
SCHEMA_FILE = REGISTRY_DIR / "catalog-registry.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def load_registry(path: Path = REGISTRY_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_entry(
    entry: dict[str, Any], *, repo_root: Path, trust: signing.TrustRoot
) -> dict[str, Any]:
    """Recompute the digest and verify the signature; refuse a mismatch either way."""
    reasons: list[str] = []
    catalog_dir = repo_root / str(entry["source_path"])

    if not catalog_dir.is_dir():
        reasons.append(f"source_path does not exist: {catalog_dir}")
        return {"id": entry.get("id"), "listed": False, "reasons": reasons}

    recomputed = signing.digest_tree(
        catalog_dir, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
    )
    if recomputed != entry.get("digest"):
        reasons.append(
            "recorded digest does not match a fresh recomputation of source_path "
            f"(recorded={entry.get('digest')!r}, recomputed={recomputed!r})"
        )

    try:
        signing.verify_catalog_directory(catalog_dir, trust)
    except signing.VerificationError as exc:
        reasons.append(f"catalog signature did not verify: {exc}")

    return {"id": entry.get("id"), "listed": not reasons, "reasons": reasons}


def run(
    registry_file: Path = REGISTRY_FILE, *, repo_root: Path = REPO_ROOT
) -> dict[str, Any]:
    schema = load_schema()
    registry = load_registry(registry_file)
    jsonschema.validate(instance=registry, schema=schema)

    trust = signing.vendored_trust()
    results = [
        evaluate_entry(entry, repo_root=repo_root, trust=trust)
        for entry in registry["catalogs"]
    ]
    return {
        "catalogs": len(results),
        "listed": [r for r in results if r["listed"]],
        "rejected": [r for r in results if not r["listed"]],
    }


def self_test() -> int:
    """A well-formed fixture entry is listed; a digest-mismatched one is refused."""
    schema = load_schema()
    registry = load_registry()
    jsonschema.validate(instance=registry, schema=schema)
    trust = signing.vendored_trust()

    real_entry = next(
        entry for entry in registry["catalogs"] if entry["id"] == "eu-ai-act"
    )

    good = evaluate_entry(real_entry, repo_root=REPO_ROOT, trust=trust)
    if not good["listed"]:
        print(
            f"CATALOG REGISTRY SELF-TEST FAILED: a well-formed entry was refused: {good['reasons']}",
            file=sys.stderr,
        )
        return 1
    print(f"  well-formed entry listed OK ({good['id']})")

    tampered = copy.deepcopy(real_entry)
    digest = tampered["digest"]
    # Flip one hex character at runtime -- a differently-valued, non-zero digest, never a literal
    # all-zeroed digest string in source text (the repository's authored-text scanner flags a
    # contiguous all-zero hex/UUID string wherever it appears in tracked source).
    flip_at = len(digest) - 1
    flipped_char = "0" if digest[flip_at] != "0" else "1"
    tampered["digest"] = digest[:flip_at] + flipped_char
    refused = evaluate_entry(tampered, repo_root=REPO_ROOT, trust=trust)
    if refused["listed"]:
        print(
            "CATALOG REGISTRY SELF-TEST FAILED: a digest-mismatched entry was listed",
            file=sys.stderr,
        )
        return 1
    print(f"  digest-mismatched entry refused OK: {refused['reasons']}")

    print("CATALOG REGISTRY SELF-TEST PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catalog_registry_check",
        description="Catalog registry gate (item 16.4, P16.8).",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="prove the accept and refuse paths"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    summary = run()
    if args.json:
        print(
            json.dumps(
                {
                    "catalogs": summary["catalogs"],
                    "listed": len(summary["listed"]),
                },
                sort_keys=True,
            )
        )
    else:
        print(f"catalogs={summary['catalogs']} listed={len(summary['listed'])}")
        for rejected in summary["rejected"]:
            print(f"  rejected {rejected['id']}: {'; '.join(rejected['reasons'])}")
    return 0 if not summary["rejected"] else 1


if __name__ == "__main__":
    sys.exit(main())
