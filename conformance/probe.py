"""Phase-3 probe-evaluation conformance check (P3.2).

Scores the frozen synthetic probe corpus (``corpus/probe/v1``) with one probe spec per oracle kind
and confirms that a tampered copy of the corpus is rejected before scoring. The engine's probe
evaluator does the work; this orchestrator asserts the whole vertical slice agrees with the corpus's
authored ground truth (every oracle finds exactly the two attack cases) and that integrity holds.

Run it as::

    cd conformance && uv run python probe.py --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from agentce import probe

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "corpus" / "probe" / "v1"
EXPECTED_FAILED = 2  # the corpus is authored with exactly two attack cases


def _tamper_rejected(corpus_dir: Path) -> bool:
    """A copy whose recorded prompt no longer matches its manifest hash must be rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "probe"
        shutil.copytree(corpus_dir, dst)
        case_file = sorted((dst / "cases").glob("*.json"))[0]
        case = json.loads(case_file.read_text(encoding="utf-8"))
        case["prompt"] = str(case.get("prompt", "")) + " (tampered)"
        case_file.write_text(json.dumps(case), encoding="utf-8")
        try:
            probe.load_corpus(dst)
        except probe.ProbeIntegrityError:
            return True
        return False


def run(corpus_dir: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus = probe.load_corpus(corpus_dir)
    shapes = probe.parse_shape_file(corpus_dir / "shapes.ttl")
    probes: list[dict[str, Any]] = []
    oracles_passed = True
    for spec_file in sorted((corpus_dir / "probes").glob("*.probe.json")):
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        result = probe.evaluate_probe(spec, corpus, shapes=shapes)
        agrees = result.outcome == "conformant" and result.failed == EXPECTED_FAILED
        oracles_passed = oracles_passed and agrees
        probes.append(
            {
                "probe": result.probe,
                "oracle": spec["oracle"]["kind"],
                "outcome": result.outcome,
                "failed": result.failed,
                "agrees": agrees,
            }
        )
    return {
        "oracles_passed": oracles_passed and len(probes) == 4,
        "hash_mismatch_rejected": _tamper_rejected(corpus_dir),
        "probes": probes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-3 probe evaluation conformance check (P3.2)."
    )
    parser.add_argument(
        "--corpus", type=Path, default=DEFAULT_CORPUS, help="the frozen probe corpus"
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    result = run(args.corpus)
    if args.json:
        print(json.dumps(result))
    else:
        status = (
            "OK"
            if result["oracles_passed"] and result["hash_mismatch_rejected"]
            else "FAIL"
        )
        print(f"PROBE CONFORMANCE {status}")
    return 0 if result["oracles_passed"] and result["hash_mismatch_rejected"] else 1


if __name__ == "__main__":
    sys.exit(main())
