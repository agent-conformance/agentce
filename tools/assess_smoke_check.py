"""Repository check: the vendored quickstart's real `assess` output is a real report.

The TypeScript and Java `quickstart.yml` jobs run `agentce assess` against the vendored quickstart
project with the built engine, then hand this script the output directory and the captured `--json`
envelope. It fails unless `assess` wrote a non-empty `assertions.json` and the envelope reports at least
one assertion and a real verdict — proof the run judged something, not a stub that exits 0 having
evaluated nothing (see #4/#6).

    assess_smoke_check.py OUT_DIR ENVELOPE_JSON   check a real run's output
    assess_smoke_check.py --self-test             prove the checker discriminates

Stdlib only. No network, no learned component.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

VALID_VERDICTS = {"conformant", "non-conformant", "incomplete"}


def check(out_dir: Path, envelope: dict) -> list[str]:
    """Every problem with an assess run's output (empty means the run is sound)."""
    problems: list[str] = []
    assertions_file = out_dir / "assertions.json"
    if not assertions_file.is_file() or assertions_file.stat().st_size == 0:
        problems.append(f"{assertions_file} is missing or empty")
    assertions = envelope.get("assertions")
    if not isinstance(assertions, int) or assertions <= 0:
        problems.append(f"envelope reports {assertions!r} assertions, expected at least 1")
    verdict = (envelope.get("summary") or {}).get("verdict")
    if verdict not in VALID_VERDICTS:
        problems.append(f"envelope reports verdict {verdict!r}, expected one of {sorted(VALID_VERDICTS)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if len(argv) != 2:
        print("usage: assess_smoke_check.py OUT_DIR ENVELOPE_JSON", file=sys.stderr)
        return 2
    out_dir = Path(argv[0])
    envelope = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    problems = check(out_dir, envelope)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"ASSESS SMOKE CHECK FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"ASSESS SMOKE CHECK OK ({envelope['assertions']} assertions; verdict {envelope['summary']['verdict']})")
    return 0


def self_test() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "assertions.json").write_bytes(b"[{}]")

        good_envelope = {"assertions": 49, "summary": {"verdict": "incomplete"}}
        problems = check(out, good_envelope)
        if problems:
            failures.append(f"good: expected no problems, got {problems}")

        if not check(out, {"assertions": 0, "summary": {"verdict": "incomplete"}}):
            failures.append("bad-zero-assertions: expected a problem, got none")
        if not check(out, {"assertions": 49, "summary": {"verdict": "bogus"}}):
            failures.append("bad-verdict: expected a problem, got none")

        empty_dir = out / "empty"
        empty_dir.mkdir()
        if not check(empty_dir, good_envelope):
            failures.append("bad-missing-file: expected a problem, got none")

    if failures:
        print("ASSESS SMOKE CHECK SELF-TEST FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("ASSESS SMOKE CHECK SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
