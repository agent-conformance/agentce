"""Corpus publication tooling — dry-run only (SPEC §11.7).

The corpus is split between this repository (the generator, rule fixtures, ground-truth READMEs, the
ECS runner, and this ``VERSIONS.md`` pin) and three Hugging Face datasets that hold the generated
output. Actual publication runs from CI with the ``huggingface`` environment secret and a required
reviewer (HUMAN_ACTIONS H1); this tool only plans that publication offline, so an agent never uploads.

    cd corpus && uv run python publish.py --dry-run

verifies that the corpus the generator produces matches its ``VERSIONS.md`` pin, prints the datasets,
their contents, licence, and visibility that a real run would publish, and prints ``DRY-RUN OK`` (exit
0) — making no network call and using no token. Without ``--dry-run`` it refuses and points to the
human action, because uploading to an external service is not the agent's to do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The version-check logic lives in the corpus package; make it importable from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus.check_versions import verify_versions  # noqa: E402

#: The three datasets the corpus publishes to, kept separate because they change for different reasons
#: and on different cadences (SPEC §11.7).
DATASETS: tuple[tuple[str, str], ...] = (
    (
        "agent-conformance/corpus",
        "evidence bundles, applicability, domain, deviations, expected, ground-truth READMEs",
    ),
    (
        "agent-conformance/golden",
        "reference engine outputs keyed by corpus revision x catalog version",
    ),
    ("agent-conformance/probes", "frozen adversarial probe corpora"),
)
LICENCE = "CC-BY-4.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="publish",
        description="Plan (dry-run) the corpus publication to Hugging Face (SPEC §11.7).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="plan the publication offline; the only supported mode for the agent",
    )
    parser.add_argument(
        "--corpus",
        default=str(Path(__file__).resolve().parent),
        help="the corpus source tree (default: this directory)",
    )
    args = parser.parse_args(argv)

    if not args.dry_run:
        print(
            "publication requires the huggingface environment secret (HUMAN_ACTIONS H1) and is run by "
            "a maintainer from CI; pass --dry-run to plan it offline.",
            file=sys.stderr,
        )
        return 3

    ok, message = verify_versions(Path(args.corpus))
    print(f"version check: {message}")
    if not ok:
        print(
            "DRY-RUN FAILED: the generated corpus does not match its VERSIONS.md pin; "
            "update corpus/VERSIONS.md (SPEC 11.7 rule 2).",
            file=sys.stderr,
        )
        return 1

    print(f"planned datasets (private until the GA update, G-5; licence {LICENCE}):")
    for name, contents in DATASETS:
        print(f"  - {name}: {contents}")
    print("no network call was made and no token was used.")
    print("DRY-RUN OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
