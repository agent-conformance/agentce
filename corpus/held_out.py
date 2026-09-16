"""Held-out and adversarial detection metrics over the full corpus (SPEC §11.2, §11.6, P3.5).

The full corpus carries two labelled evaluation subsets beside the authored core: a **held-out** set of
fault-bearing projects and an **adversarial** set of near-miss projects (see the generator's
``group`` tag). This module runs each project through the engine and scores it against the ground
truth the corpus manifest carries:

* **heldout_recall** — the fraction of the held-out set's seeded faults (the controls a project is
  authored to make non-conformant) the engine flags non-conformant. P3.5 requires ``>= 0.95``.
* **adversarial_match** — whether the engine's outcome equals the authored ground truth for every
  control of every adversarial project. P3.5 requires ``true``.

``python -m corpus.held_out --corpus <dir> --json`` prints the metrics; the source tree is
materialised on demand as the full set, so the corpus output need never be committed (SPEC §11.7).
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


def _materialise_full(corpus_dir: Path) -> Path:
    """Return a directory holding the full-set ``corpus-manifest.json``, generating it if needed."""
    manifest = corpus_dir / "corpus-manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("set") == "full":
            return corpus_dir
    if (corpus_dir / "generator" / "generate.py").is_file():
        from corpus.generator.generate import build_corpus

        out = Path(tempfile.mkdtemp(prefix="corpus-heldout-"))
        build_corpus(out, "full")
        return out
    raise SystemExit(
        f"{corpus_dir} has neither a full corpus-manifest.json nor a generator; nothing to score."
    )


def _engine_outcomes(
    corpus_root: Path, project_id: str, subject: str, out_dir: Path
) -> dict[str, str]:
    # The assess helper prints a human summary; keep stdout clean so --json is machine-readable.
    with contextlib.redirect_stdout(io.StringIO()):
        rc = assess_project(corpus_root, project_id, out_dir)
    if rc != 0:
        raise SystemExit(f"assessing {project_id} failed with exit {rc}")
    assertions = json.loads((out_dir / "assertions.json").read_text(encoding="utf-8"))
    return {a["control"]: a["outcome"] for a in assertions if a["subject"] == subject}


def compute_held_out(corpus_dir: Path) -> dict[str, Any]:
    """Score the held-out and adversarial subsets of the full corpus against the engine (P3.5)."""
    corpus_root = _materialise_full(corpus_dir)
    manifest = json.loads(
        (corpus_root / "corpus-manifest.json").read_text(encoding="utf-8")
    )
    projects = sorted(manifest.get("projects", []), key=lambda p: str(p["id"]))
    held_out = [p for p in projects if p.get("group") == "held-out"]
    adversarial = [p for p in projects if p.get("group") == "adversarial"]

    seeded = detected = 0
    adversarial_controls = 0
    adversarial_match = True
    with tempfile.TemporaryDirectory(prefix="corpus-heldout-out-") as tmp:
        for project in held_out:
            pid = str(project["id"])
            subject = str(project["subject"])
            expected: dict[str, str] = project["expected"]
            got = _engine_outcomes(
                corpus_root, pid, subject, Path(tmp) / pid.replace("/", "__")
            )
            for control, want in expected.items():
                if want == "non-conformant":
                    seeded += 1
                    detected += int(got.get(control) == "non-conformant")
        for project in adversarial:
            pid = str(project["id"])
            subject = str(project["subject"])
            expected = project["expected"]
            got = _engine_outcomes(
                corpus_root, pid, subject, Path(tmp) / pid.replace("/", "__")
            )
            for control, want in expected.items():
                adversarial_controls += 1
                if got.get(control) != want:
                    adversarial_match = False

    recall = 1.0 if seeded == 0 else detected / seeded
    return {
        "heldout_recall": recall,
        "adversarial_match": adversarial_match,
        "heldout_projects": len(held_out),
        "adversarial_projects": len(adversarial),
        "heldout_seeded": seeded,
        "heldout_detected": detected,
        "adversarial_controls": adversarial_controls,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus.held_out",
        description="Score the held-out and adversarial corpus subsets against the engine (P3.5).",
    )
    parser.add_argument(
        "--corpus", required=True, help="the corpus directory (full set or source tree)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    metrics = compute_held_out(Path(args.corpus))
    if args.json:
        print(json.dumps(metrics, sort_keys=True))
    else:
        print(
            f"heldout_recall={metrics['heldout_recall']} "
            f"adversarial_match={metrics['adversarial_match']} "
            f"(held_out={metrics['heldout_projects']}, adversarial={metrics['adversarial_projects']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
