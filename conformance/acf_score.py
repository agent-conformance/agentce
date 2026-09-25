"""AgentCE's own honest ACF scorecard: `conformance/acf_score.py` runs every
`conformance/acf/matrix.yaml` entry's `test` and scores the capability 0-3 on the Agent Conformance
Framework's own maturity scale (0 Absent, 1 Asserted, 2 Partial, 3 Complete), then emits a
canonical-JSON scorecard plus a human table -- the artifact AgentCE publishes beside the framework
itself, dog-fooding the standard (AgentCE's own coverage assessed the way AgentCE assesses agents:
deterministic, offline, evidence-based, honest about gaps).

Scoring rule (the anti-hype rubric matrix.schema.json already documents on `level_target`, applied
here):

  * no `test` (null)         -> min(level_target, 1). An unverifiable claim never scores above
                                 Asserted (1), regardless of what `level_target` claims.
  * `test` present and FAILS -> 0 (Absent). A capability whose own proof does not pass today is not
                                 real today; "no capability scores 3 without a passing discriminating
                                 test" is a hard floor at 0, not a soft cap, because a failing test is
                                 a *disproof*, not merely an absence of proof.
  * `test` present and PASSES -> `level_target`, UNLESS `level_target == 3` and `evidence_ref` does
                                 not resolve to a real, on-disk path (see `_evidence_resolves`), in
                                 which case the score is capped at Partial (2). A level-3 (Complete)
                                 score always carries a resolvable evidence pointer -- no exceptions.

"Passes" means the same {exit, expect_ok} signal `acf_matrix_check.Outcome` already defines: exit 0,
and (when `test.expect` is set) the expected substring was found in combined stdout+stderr.

This module deliberately does not re-prove that a `test.cmd` is *itself* deterministic across
back-to-back runs (order/locale/clock-independent) -- `acf_matrix_check.py --check-fixtures` (item
17.2, this item's own dependency) already runs every real entry's `cmd` twice and rejects a
mismatch, so re-running the same, sometimes expensive, commands a second time here would duplicate an
existing, already-guarded proof rather than add one (`matrix.yaml`'s own header: this matrix "never
invents a second, competing proof of something already guarded elsewhere"). What this module *does*
independently prove deterministic -- via `--self-test`, using cheap synthetic entries rather than the
real 35 -- is its own scoring and serialization logic: given a fixed set of per-entry outcomes, does
`build_scorecard` always compute the same scores and serialize them to the same bytes. That is the
literal claim "two runs against the same corpus state produce a byte-identical canonical scorecard"
makes: a property of this module's pure computation, not of subprocess execution flakiness (which is
a different module's job).

The "canonical JSON" here is `json.dumps(data, indent=2, sort_keys=True)` -- the same convention
`acf_matrix_check.py --record-golden` already uses for `fixtures-golden.json` in this same directory.
This is deliberately not `engines/python/agentce/canonical.py` (the strict RFC 8785 JCS evidence
profile): that module exists to make the three *engines'* assertion/report outputs byte-identical
under SPEC's canonical-form invariant, and `matrix.schema.json`'s own header already states this
matrix (and, by the same reasoning, its scorecard) "is an internal QA artifact of the conformance
suite, not a spec vocabulary schema" -- it carries no canonical agent-conformance.org IRI and is
never vendored into the three engines, so the SPEC canonical-form module is the wrong tool: it forbids
non-integer JSON numbers, and this scorecard's per-dimension mean is deliberately a rounded float.

The score-only-rises ratchet: `conformance/acf/scorecard.json` is the committed prior scorecard. Every
invocation (other than `--self-test`) recomputes a fresh scorecard and compares each capability's
score against the committed file's score for the same id; if any id's score fell, the run refuses
(prints a `acf.score_regression` violation per id and exits 1) rather than silently publishing a
regression. `--write` additionally overwrites the committed file with the fresh scorecard, but only
when no regression was found -- the same refuse-before-write discipline `acf_matrix_check.py
--record-golden` follows for `fixtures-golden.json`, except `--write` here still runs the ratchet
check first (an accidental regression can never be "fixed" by simply re-recording it as the new
baseline).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from acf_matrix_check import Outcome, load_matrix, run_entry_test

_ROOT = Path(__file__).resolve().parent.parent
_SCORECARD = _ROOT / "conformance" / "acf" / "scorecard.json"
_DEFAULT_CMD_TIMEOUT_S = 300

_LEVEL_LABELS = {0: "Absent", 1: "Asserted", 2: "Partial", 3: "Complete"}


def _evidence_resolves(evidence_ref: str | None, root: Path) -> bool:
    """True iff at least one ``;``-separated segment of ``evidence_ref``, read up to its first
    whitespace and any trailing ``::symbol`` anchor, exists as a real path under ``root``. Every
    level-3 entry's real `evidence_ref` mixes plain paths with non-path asides (a CLI flag name, a
    parenthetical annotation, a doc anchor) in the same field -- "at least one segment resolves" is
    what a human reading the field actually means by "pointing at the real check/evidence", and was
    verified by hand against all 22 of today's real level-3 entries before being written here: every
    one carries at least one segment that is a plain, existing repository path."""
    if not evidence_ref:
        return False
    for segment in evidence_ref.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        token = segment.split()[0] if segment.split() else segment
        token = token.split("::")[0]
        if token and (root / token).exists():
            return True
    return False


def score_entry(
    entry: dict[str, Any], *, root: Path, timeout_s: int = _DEFAULT_CMD_TIMEOUT_S
) -> dict[str, Any]:
    """Run (or, for a `test: null` entry, decline to run) one matrix entry's test and return its
    scorecard record. Never raises on a failing or timing-out test -- that is a score of 0, not an
    exception; a `subprocess.TimeoutExpired` is treated the same as any other non-passing outcome."""
    entry_id = entry["id"]
    level_target = entry["level_target"]
    test = entry.get("test")
    evidence_ref = entry.get("evidence_ref")

    outcome: Outcome | None = None
    if test is None:
        score = min(level_target, 1)
        capped_reason = (
            None
            if score >= level_target
            else "no executable test for this capability -- capped at Asserted (1), the anti-hype "
            "rule (an unverifiable claim never scores above Asserted)"
        )
    else:
        try:
            outcome = run_entry_test(test, cwd=root, timeout_s=timeout_s)
        except subprocess.TimeoutExpired:
            outcome = Outcome(-1, None)
        passed = outcome.exit == 0 and outcome.expect_ok is not False
        if not passed:
            score = 0
            capped_reason = (
                f"test.cmd did not pass (exit={outcome.exit}, expect_ok={outcome.expect_ok}) -- "
                "no capability scores above Absent (0) without a passing test"
            )
        else:
            score = level_target
            capped_reason = None
            if score == 3 and not _evidence_resolves(evidence_ref, root):
                score = 2
                capped_reason = (
                    "level_target 3 (Complete) claimed but evidence_ref does not resolve to a real, "
                    "on-disk path -- capped at Partial (2); a level-3 score always carries a "
                    "resolvable evidence pointer, no exceptions"
                )

    return {
        "id": entry_id,
        "dimension": entry["dimension"],
        "capability": entry["capability"],
        "level_target": level_target,
        "score": score,
        "level_label": _LEVEL_LABELS[score],
        "evidence_ref": evidence_ref,
        "outcome": outcome.as_dict() if outcome is not None else None,
        "capped_reason": capped_reason,
    }


def build_scorecard(
    matrix: dict[str, Any], *, root: Path, timeout_s: int = _DEFAULT_CMD_TIMEOUT_S
) -> dict[str, Any]:
    capabilities = [
        score_entry(entry, root=root, timeout_s=timeout_s)
        for entry in matrix.get("entries", [])
    ]

    by_dimension: dict[str, list[int]] = {}
    for cap in capabilities:
        by_dimension.setdefault(cap["dimension"], []).append(cap["score"])
    dimension_summary = {
        dimension: {
            "count": len(scores),
            "mean": round(sum(scores) / len(scores), 4),
        }
        for dimension, scores in by_dimension.items()
    }

    all_scores = [cap["score"] for cap in capabilities]
    overall = {
        "count": len(all_scores),
        "mean": round(sum(all_scores) / len(all_scores), 4) if all_scores else 0,
        "min": min(all_scores) if all_scores else 0,
        "max": max(all_scores) if all_scores else 0,
    }

    return {
        "version": 1,
        "generated_from": "conformance/acf/matrix.yaml",
        "capabilities": capabilities,
        "dimension_summary": dimension_summary,
        "overall": overall,
    }


def canonical_bytes(scorecard: dict[str, Any]) -> bytes:
    return (json.dumps(scorecard, indent=2, sort_keys=True) + "\n").encode("utf-8")


def render_human_table(scorecard: dict[str, Any]) -> str:
    lines = ["id      dim             score  label     level_target  evidence_ref"]
    for cap in scorecard["capabilities"]:
        lines.append(
            f"{cap['id']:<7} {cap['dimension']:<15} {cap['score']:<6} "
            f"{cap['level_label']:<9} {cap['level_target']:<13} {cap['evidence_ref'] or '-'}"
        )
    lines.append("")
    lines.append("dimension        count  mean")
    for dimension, summary in sorted(scorecard["dimension_summary"].items()):
        lines.append(f"{dimension:<17} {summary['count']:<6} {summary['mean']}")
    overall = scorecard["overall"]
    lines.append("")
    lines.append(
        f"overall: {overall['count']} capabilities, mean {overall['mean']} "
        f"(min {overall['min']}, max {overall['max']})"
    )
    return "\n".join(lines)


def compare_against_committed(
    fresh: dict[str, Any], committed_path: Path = _SCORECARD
) -> list[str]:
    """Return one violation string per capability whose score fell versus the committed scorecord.
    Empty means no regression -- including when the committed file does not exist yet (nothing to
    ratchet against) or when a fresh capability id has no prior record (a brand-new matrix entry)."""
    if not committed_path.exists():
        return []
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    prior_scores = {
        cap["id"]: cap["score"] for cap in committed.get("capabilities", [])
    }
    violations = []
    for cap in fresh["capabilities"]:
        prior = prior_scores.get(cap["id"])
        if prior is not None and cap["score"] < prior:
            violations.append(f"{cap['id']}: score fell from {prior} to {cap['score']}")
    return violations


# --- Self-test: proves the scoring rule, the serialization, and the ratchet each discriminate. ---


def _entry(
    entry_id: str,
    *,
    level_target: int,
    cmd: str | None,
    expect: str | None = None,
    evidence_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "dimension": "coverage",
        "capability": "Self-test capability",
        "statement": "Self-test only.",
        "level_target": level_target,
        "test": None
        if cmd is None
        else {"cmd": cmd, "fixture": None, "expect": expect},
        "evidence_ref": evidence_ref,
    }


def _self_test_scoring() -> list[str]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        real_file = root / "real_evidence.txt"
        real_file.write_text("proof", encoding="utf-8")

        cases: dict[str, dict[str, Any]] = {
            "no test, level_target 3 -> capped at Asserted (1)": {
                "entry": _entry("SELF-1", level_target=3, cmd=None),
                "expect_score": 1,
            },
            "test passes, level_target 3, evidence_ref resolves -> Complete (3)": {
                "entry": _entry(
                    "SELF-2",
                    level_target=3,
                    cmd="true",
                    evidence_ref="real_evidence.txt",
                ),
                "expect_score": 3,
            },
            "test passes, level_target 3, evidence_ref does NOT resolve -> capped at Partial (2)": {
                "entry": _entry(
                    "SELF-3",
                    level_target=3,
                    cmd="true",
                    evidence_ref="does/not/exist.txt",
                ),
                "expect_score": 2,
            },
            "test passes, level_target 3, evidence_ref null -> capped at Partial (2)": {
                "entry": _entry(
                    "SELF-4", level_target=3, cmd="true", evidence_ref=None
                ),
                "expect_score": 2,
            },
            "test FAILS (nonzero exit), level_target 3 -> Absent (0), never the target": {
                "entry": _entry(
                    "SELF-5",
                    level_target=3,
                    cmd="false",
                    evidence_ref="real_evidence.txt",
                ),
                "expect_score": 0,
            },
            "test exits 0 but expect string missing -> treated as failing -> Absent (0)": {
                "entry": _entry(
                    "SELF-6",
                    level_target=3,
                    cmd="python3 -c \"print('actual')\"",
                    expect="expected-substring",
                    evidence_ref="real_evidence.txt",
                ),
                "expect_score": 0,
            },
            "test passes, level_target 2, evidence_ref irrelevant -> Partial (2) unchanged": {
                "entry": _entry(
                    "SELF-7", level_target=2, cmd="true", evidence_ref=None
                ),
                "expect_score": 2,
            },
            "test times out -> Absent (0)": {
                "entry": _entry("SELF-8", level_target=3, cmd="sleep 5"),
                "expect_score": 0,
                "timeout_s": 1,
            },
            "no test, level_target 0 -> Absent (0), min() never raises a floor": {
                "entry": _entry("SELF-9", level_target=0, cmd=None),
                "expect_score": 0,
            },
        }

        for name, case in cases.items():
            record = score_entry(
                case["entry"], root=root, timeout_s=case.get("timeout_s", 30)
            )
            if record["score"] != case["expect_score"]:
                failures.append(
                    f"{name}: expected score={case['expect_score']}, got {record['score']!r} "
                    f"({record['capped_reason']!r})"
                )

    if not failures:
        print(f"acf_score self-test: {len(cases)} scoring-rule cases discriminate")
    return failures


def _self_test_determinism() -> list[str]:
    """Given a fixed set of synthetic entries (no subprocess variance -- every `cmd` is a pure,
    instantaneous literal), two independent `build_scorecard` calls must serialize to identical
    bytes: this is the "two runs, byte-identical canonical scorecard" property, isolated to this
    module's own computation and serialization rather than to subprocess execution timing."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "evidence.txt").write_text("x", encoding="utf-8")
        matrix = {
            "version": 1,
            "entries": [
                _entry(
                    "DET-1", level_target=3, cmd="true", evidence_ref="evidence.txt"
                ),
                _entry("DET-2", level_target=2, cmd="false"),
                _entry("DET-3", level_target=1, cmd=None),
            ],
        }
        first = canonical_bytes(build_scorecard(matrix, root=root))
        second = canonical_bytes(build_scorecard(matrix, root=root))
        if first != second:
            failures.append(
                f"determinism: two build_scorecard runs produced different bytes "
                f"({len(first)} vs {len(second)} bytes)"
            )
        else:
            print(
                "acf_score self-test: two runs produce a byte-identical canonical scorecard"
            )
    return failures


def _self_test_ratchet() -> list[str]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        matrix = {
            "version": 1,
            "entries": [_entry("RAT-1", level_target=2, cmd="true")],
        }
        fresh = build_scorecard(matrix, root=root)  # RAT-1 scores 2

        no_prior = root / "no-prior-scorecard.json"
        if compare_against_committed(fresh, no_prior) != []:
            failures.append(
                "no committed file: expected no violations (nothing to ratchet against)"
            )

        regressed_committed = root / "regressed-scorecard.json"
        regressed_committed.write_text(
            json.dumps({"capabilities": [{"id": "RAT-1", "score": 3}]}),
            encoding="utf-8",
        )
        violations = compare_against_committed(fresh, regressed_committed)
        if not violations or "RAT-1" not in violations[0]:
            failures.append(
                f"planted regression (committed=3, fresh=2): expected a RAT-1 violation, got {violations!r}"
            )

        same_committed = root / "same-scorecard.json"
        same_committed.write_text(
            json.dumps({"capabilities": [{"id": "RAT-1", "score": 2}]}),
            encoding="utf-8",
        )
        if compare_against_committed(fresh, same_committed) != []:
            failures.append("no regression (committed==fresh): expected no violations")

        risen_committed = root / "risen-scorecard.json"
        risen_committed.write_text(
            json.dumps({"capabilities": [{"id": "RAT-1", "score": 1}]}),
            encoding="utf-8",
        )
        if compare_against_committed(fresh, risen_committed) != []:
            failures.append("score rose (committed=1, fresh=2): expected no violations")

        new_id_committed = root / "new-id-scorecard.json"
        new_id_committed.write_text(
            json.dumps({"capabilities": [{"id": "SOME-OTHER-ID", "score": 3}]}),
            encoding="utf-8",
        )
        if compare_against_committed(fresh, new_id_committed) != []:
            failures.append(
                "fresh id with no prior record: expected no violations (nothing to compare)"
            )

    if not failures:
        print(
            "acf_score self-test: the score-only-rises ratchet refuses a planted regression and "
            "passes a same/risen/new-id committed scorecard"
        )
    return failures


def _self_test_human_table() -> list[str]:
    """`render_human_table` is a real, load-bearing part of the emitted scorecard ("canonical JSON +
    human table"), not decoration -- prove it actually reflects the data rather than being able to
    silently regress to an empty string, or a table with no evidence column, while every other
    self-test case stays green."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "evidence.txt").write_text("x", encoding="utf-8")
        matrix = {
            "version": 1,
            "entries": [
                _entry(
                    "TBL-1", level_target=3, cmd="true", evidence_ref="evidence.txt"
                ),
                _entry("TBL-2", level_target=2, cmd="true"),
                _entry("TBL-3", level_target=1, cmd=None),
            ],
        }
        scorecard = build_scorecard(matrix, root=root)
        table = render_human_table(scorecard)

        if not table.strip():
            failures.append(
                "render_human_table returned empty output for a real scorecard"
            )
        for entry_id in ("TBL-1", "TBL-2", "TBL-3"):
            if entry_id not in table:
                failures.append(
                    f"render_human_table: {entry_id} missing from the table"
                )
        if "evidence.txt" not in table:
            failures.append(
                "render_human_table: TBL-1's evidence_ref ('evidence.txt') missing from the table "
                "-- a level-3 row must show its evidence pointer, not just its score"
            )
        if "3" not in table or "Complete" not in table:
            failures.append(
                "render_human_table: TBL-1's score/label (3/Complete) missing from the table"
            )
        for dimension in scorecard["dimension_summary"]:
            if dimension not in table:
                failures.append(
                    f"render_human_table: dimension summary row for {dimension!r} missing"
                )
        overall = scorecard["overall"]
        if str(overall["mean"]) not in table:
            failures.append("render_human_table: overall mean missing from the table")

    if not failures:
        print(
            "acf_score self-test: the human table carries every capability, its evidence_ref, and the "
            "dimension/overall summary -- not a blank or gutted renderer"
        )
    return failures


def self_test() -> int:
    failures: list[str] = []
    failures += _self_test_scoring()
    failures += _self_test_determinism()
    failures += _self_test_ratchet()
    failures += _self_test_human_table()
    failures += _self_test_gate()

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    return 0


def _gate(scorecard: dict[str, Any], committed_path: Path, *, write: bool) -> int:
    """The ratchet gate: refuse (return 1, printing one `acf.score_regression` violation per
    capability) if `scorecard` scores any capability below `committed_path`'s recorded score for the
    same id; otherwise return 0, additionally overwriting `committed_path` when `write` is set.
    Factored out of `main()` so the exit-code behavior itself -- not just `compare_against_committed`'s
    return value -- is directly self-testable without touching argv or the module-level `_ROOT`/
    `_SCORECARD` globals."""
    violations = compare_against_committed(scorecard, committed_path)
    if violations:
        for violation in violations:
            print(f"VIOLATION [acf.score_regression]: {violation}", file=sys.stderr)
        return 1
    if write:
        committed_path.write_bytes(canonical_bytes(scorecard))
        print(f"acf score: wrote {committed_path}", file=sys.stderr)
    else:
        print(
            "acf score: ratchet ok, no capability's committed score fell",
            file=sys.stderr,
        )
    return 0


def _self_test_gate() -> list[str]:
    """Proves `main()`'s own exit-code contract, not just `compare_against_committed`'s return value:
    a real regression must make the whole run exit nonzero, and a clean or improving run must exit 0 --
    including the `--write` path, which must not write when there was a regression. Gutting `_gate`'s
    `if violations: ... return 1` branch (leaving CI to silently publish a regression) is exactly what
    this case exists to catch, the same defect class the human-table self-test catches for
    `render_human_table`."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scorecard = {"capabilities": [{"id": "GATE-1", "score": 2}]}

        regressed = root / "regressed.json"
        regressed.write_text(
            json.dumps({"capabilities": [{"id": "GATE-1", "score": 3}]}),
            encoding="utf-8",
        )
        exit_code = _gate(scorecard, regressed, write=False)
        if exit_code == 0:
            failures.append(
                "_gate: a real regression (committed=3, fresh=2) returned exit 0"
            )

        write_target = root / "regressed-write.json"
        write_target.write_text(
            json.dumps({"capabilities": [{"id": "GATE-1", "score": 3}]}),
            encoding="utf-8",
        )
        before = write_target.read_text(encoding="utf-8")
        _gate(scorecard, write_target, write=True)
        if write_target.read_text(encoding="utf-8") != before:
            failures.append(
                "_gate: --write overwrote the committed file despite a regression"
            )

        clean = root / "clean.json"
        clean.write_text(
            json.dumps({"capabilities": [{"id": "GATE-1", "score": 2}]}),
            encoding="utf-8",
        )
        exit_code = _gate(scorecard, clean, write=False)
        if exit_code != 0:
            failures.append(
                "_gate: no regression (committed==fresh) returned nonzero exit"
            )

        write_ok = root / "clean-write.json"
        write_ok.write_text(
            json.dumps({"capabilities": [{"id": "GATE-1", "score": 1}]}),
            encoding="utf-8",
        )
        exit_code = _gate(scorecard, write_ok, write=True)
        if exit_code != 0:
            failures.append("_gate: an improvement with --write returned nonzero exit")
        if (
            json.loads(write_ok.read_text(encoding="utf-8"))["capabilities"][0]["score"]
            != 2
        ):
            failures.append(
                "_gate: --write did not actually persist the fresh, improved score"
            )

    if not failures:
        print(
            "acf_score self-test: main()'s own gate exits nonzero on a real regression, exits 0 "
            "otherwise, and --write never persists a regression"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "After a successful ratchet check (no capability's score fell versus the committed "
            "conformance/acf/scorecard.json), overwrite it with the fresh scorecard. Maintenance-only: "
            "never invoked by --self-test or by the CI/eval gate, which only checks."
        ),
    )
    parser.add_argument(
        "--timeout-s", type=int, default=_DEFAULT_CMD_TIMEOUT_S, dest="timeout_s"
    )
    parsed = parser.parse_args(args)

    if parsed.self_test:
        return self_test()

    matrix = load_matrix()
    scorecard = build_scorecard(matrix, root=_ROOT, timeout_s=parsed.timeout_s)

    print(render_human_table(scorecard), file=sys.stderr)
    sys.stdout.buffer.write(canonical_bytes(scorecard))

    return _gate(scorecard, _SCORECARD, write=parsed.write)


if __name__ == "__main__":
    raise SystemExit(main())
