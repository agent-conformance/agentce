#!/usr/bin/env python3
"""Accessibility-claim honesty gate.

When the website workflow's `a11y` job pauses the full-page axe scan (AGENTCE_SKIP_A11Y set to anything
other than "0" -- the same rule scripts/check-a11y.mjs applies), the accessibility statement, the VPAT,
the manual testing protocol and the claims-register entry must not say the scan runs automatically in CI
on every page or every change, and each must say plainly that the scan is paused and conformance is
verified manually, downstream. Run from the repository root.

Usage:
  python3 website/scripts/check-a11y-claims.py             Check the real workflow and sources.
  python3 website/scripts/check-a11y-claims.py --self-test Prove each rule can fail on hostile fixtures.
"""
import re
import sys
import tempfile
from pathlib import Path

WORKFLOW = ".github/workflows/website.yml"
SOURCES = [
    "website/src/content/docs/docs/accessibility-statement.md",
    "website/src/content/docs/docs/vpat.md",
    "website/src/content/docs/docs/accessibility-testing-protocol.md",
    "tools/claims/register.yaml",
]

SKIP_RE = re.compile(r"AGENTCE_SKIP_A11Y:\s*[\"']?([^\s\"']*)[\"']?\s*$", re.MULTILINE)
SCAN = re.compile(r"\b(scan(s|ned|ning)?|axe(-core)?)\b", re.I)
CI = re.compile(r"\b(ci|continuous integration|every (change|commit|push|pull request)|(every|all|each) (built )?pages?)\b", re.I)
RUNS = re.compile(r"\b(runs|ran|running|scans|scanned|scanning|executes?|executed|enforced|enforces)\b", re.I)
PAUSE = re.compile(r"\b(paused|skipped|resum\w*|not run|does not run|no longer|disabled)\b", re.I)
ALWAYS = re.compile(r"\b(never|always|not paused|not skipped|isn'?t paused)\b", re.I)
ACK = re.compile(r"paused\b.{0,60}\b(ci|continuous integration)|\b(ci|continuous integration)\b.{0,60}\bpaused|manually,?\s+downstream", re.I)


def paused(workflow_text: str) -> bool:
    return any(v not in ("", "0") for v in SKIP_RE.findall(workflow_text))


def defects(workflow_text: str, docs: dict) -> list:
    if not paused(workflow_text):
        return []
    bad = []
    for name, raw in docs.items():
        text = re.sub(r"\s+", " ", raw)
        for sentence in re.split(r"(?<=[.!?;])\s+|\s+-\s+|\s*\|\s*", text):
            if SCAN.search(sentence) and CI.search(sentence):
                if (RUNS.search(sentence) and not PAUSE.search(sentence)) or ALWAYS.search(sentence):
                    bad.append(f"{name}: asserts the full scan runs in CI / on every page: {sentence[:100]!r}")
                    break
        if not ACK.search(text):
            bad.append(f"{name}: does not say the full scan is paused and verified manually, downstream")
    return bad


def run(root: Path) -> int:
    wf, missing = root / WORKFLOW, [s for s in SOURCES if not (root / s).exists()]
    if not wf.exists() or missing:
        print(f"RED: missing {WORKFLOW if not wf.exists() else missing}", file=sys.stderr)
        return 1
    bad = defects(wf.read_text(encoding="utf-8"), {s: (root / s).read_text(encoding="utf-8") for s in SOURCES})
    if bad:
        print("RED: " + "; ".join(bad), file=sys.stderr)
        return 1
    print("OK: accessibility claims match the workflow's real behaviour")
    return 0


def self_test() -> int:
    good = "The full-page scan is currently paused in CI; conformance is verified manually, downstream."
    wf = 'env:\n  AGENTCE_SKIP_A11Y: "1"\n'
    cases = [
        ("good passes", wf, good, 0),
        ("stale claim fails", wf, good + " Axe runs in CI on every page.", 1),
        ("reordered claim fails", wf, good + " In CI, on every page, the scan runs.", 1),
        ("scanned wording fails", wf, good + " Every page is scanned in CI.", 1),
        ("negated pause fails", wf, good + " The scan is never paused in CI on any page.", 1),
        ("missing acknowledgement fails", wf, "Accessibility notes.", 1),
        ("'false' value still counts as paused", 'AGENTCE_SKIP_A11Y: "false"\n', "Axe runs in CI on every page.", 1),
        ("unpaused scan needs no acknowledgement", "env: {}\n", "Axe runs in CI on every page.", 0),
    ]
    failed = 0
    for label, workflow, doc, want in cases:
        got = 1 if defects(workflow, {"fixture": doc}) else 0
        if got != want:
            print(f"self-test FAIL: {label} (expected {want}, got {got})", file=sys.stderr)
            failed += 1
    with tempfile.TemporaryDirectory() as tmp:
        if run(Path(tmp)) != 1:
            print("self-test FAIL: empty tree must be refused", file=sys.stderr)
            failed += 1
    print("self-test OK" if not failed else f"self-test: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else run(Path(".")))
