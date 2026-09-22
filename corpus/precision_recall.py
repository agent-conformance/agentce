"""Detection and precision metrics over the corpus (SPEC §11.6).

For every automated control the corpus yields positives (seeded faults — the outcomes a variant is
authored to make non-conformant) and negatives (the controls of the known-pass projects). This module
runs every project through the engine and scores the engine's outcomes against the authored ground
truth carried in the corpus manifest:

* **recall** — the fraction of seeded faults the engine flags non-conformant. A rule that misses a
  seeded fault is broken; the base-catalog release gate requires recall 1.0 on the authored set.
* **fpr_rung2** — the false-positive rate on the known-pass projects (every base-catalog control is
  rung 2): the fraction of known-pass controls the engine wrongly flags non-conformant. The gate
  requires 0.0 for rung-2 rules.
* **scored_controls** — the distinct controls the metric actually scores (counted for recall, or
  carried by a known-pass project as a negative). The authored corpus seeds faults for a subset of the
  catalog's automated controls, not all of them; this names exactly which ones so "recall 1.0 / FPR
  0.0" is never read as broader coverage than it is (SPEC §11.6).

``python -m corpus.precision_recall --corpus <dir> --json`` prints ``{"recall": …, "fpr_rung2": …, …}``
(the interface the phase-1 eval's P1.3 check calls); the source tree is materialised on demand, so the
corpus output need never be committed (SPEC §11.7).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from corpus.assess_one import assess_project


def _materialise(corpus_dir: Path) -> Path:
    """Return a directory holding ``corpus-manifest.json``, generating from the source tree if needed."""
    if (corpus_dir / "corpus-manifest.json").is_file():
        return corpus_dir
    if (corpus_dir / "generator" / "generate.py").is_file():
        from corpus.generator.generate import build_corpus

        out = Path(tempfile.mkdtemp(prefix="corpus-pr-"))
        build_corpus(out, "v1")
        return out
    raise SystemExit(
        f"{corpus_dir} has neither a corpus-manifest.json nor a generator; nothing to score."
    )


def _engine_outcomes(
    corpus_root: Path, project_id: str, subject: str, out_dir: Path
) -> dict[str, str]:
    # The assess helper prints a human summary; keep stdout clean so --json is machine-readable.
    with contextlib.redirect_stdout(io.StringIO()):
        rc = assess_project(corpus_root, project_id, out_dir)
    if rc not in (0,):
        raise SystemExit(f"assessing {project_id} failed with exit {rc}")
    assertions = json.loads((out_dir / "assertions.json").read_text(encoding="utf-8"))
    return {a["control"]: a["outcome"] for a in assertions if a["subject"] == subject}


def compute_precision_recall(
    corpus_dir: Path, family: str | None = None
) -> dict[str, Any]:
    """Run every project through the engine and return the detection/precision metrics (SPEC §11.6).

    ``family`` restricts scoring to controls whose id starts with that family prefix (e.g. ``REC``);
    the default scores every control."""
    corpus_root = _materialise(corpus_dir)
    manifest = json.loads(
        (corpus_root / "corpus-manifest.json").read_text(encoding="utf-8")
    )

    seeded = detected = negatives = false_positives = 0
    scored_controls: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="corpus-pr-out-") as tmp:
        for project in sorted(manifest.get("projects", []), key=lambda p: str(p["id"])):
            pid = str(project["id"])
            subject = str(project["subject"])
            expected: dict[str, str] = project["expected"]
            out_dir = Path(tmp) / pid.replace("/", "__")
            got = _engine_outcomes(corpus_root, pid, subject, out_dir)
            for control, want in expected.items():
                if family and not control.startswith(family):
                    continue
                observed = got.get(control)
                if want == "non-conformant":
                    seeded += 1
                    detected += int(observed == "non-conformant")
                    scored_controls.add(control)
                if project.get("variant") == "known-pass":
                    negatives += 1
                    false_positives += int(observed == "non-conformant")
                    scored_controls.add(control)

    recall = 1.0 if seeded == 0 else detected / seeded
    fpr_rung2 = 0.0 if negatives == 0 else false_positives / negatives
    return {
        "recall": recall,
        "fpr_rung2": fpr_rung2,
        "seeded_faults": seeded,
        "detected": detected,
        "known_pass_controls": negatives,
        "false_positives": false_positives,
        "scored_controls": sorted(scored_controls),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus.precision_recall",
        description="Score the engine's outcomes against the corpus ground truth (SPEC §11.6).",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="the corpus directory (generated or source tree)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    metrics = compute_precision_recall(Path(args.corpus))
    if args.json:
        print(json.dumps(metrics, sort_keys=True))
    else:
        print(
            f"recall={metrics['recall']} fpr_rung2={metrics['fpr_rung2']} "
            f"(seeded_faults={metrics['seeded_faults']}, detected={metrics['detected']}, "
            f"known_pass_controls={metrics['known_pass_controls']}, false_positives={metrics['false_positives']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
