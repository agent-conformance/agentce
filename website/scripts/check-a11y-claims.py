#!/usr/bin/env python3
"""Accessibility-claim honesty check over the real published statements.

The full-page axe scan is paused in continuous integration (AGENTCE_SKIP_A11Y in the website workflow's
`a11y` job) and conformance is verified manually, downstream. The accessibility statement, the VPAT, the
manual testing protocol and the claims register must therefore not repeat the sentences that once said the
scan runs on every page or every change, and each must say the scan is paused and verified manually,
downstream. Run from the repository root.

Usage:
  python3 website/scripts/check-a11y-claims.py             Check the real sources.
  python3 website/scripts/check-a11y-claims.py --self-test Prove the check can fail on stale wording.
"""

import re
import sys
import tempfile
from pathlib import Path

SOURCES = [
    "website/src/content/docs/docs/accessibility-statement.md",
    "website/src/content/docs/docs/vpat.md",
    "website/src/content/docs/docs/accessibility-testing-protocol.md",
    "tools/claims/register.yaml",
]

# Sentence fragments the published sources carried while the scan ran on every change, whitespace-collapsed.
STALE = [
    "scans every published page in both the light and dark themes on every change",
    "currently reports zero violations against the WCAG 2.2 A/AA tag set",
    "accessibility gate that runs in continuous integration on every page, in both themes, on every change",
    "run in continuous integration on every page in both themes",
    "scan (every page, both themes, in CI) reports no violations",
    "rule reports no violations across every page and theme",
    "scan reports no violations; not yet manually verified",
]
PAUSED = re.compile(r"\bpaused\b", re.IGNORECASE)
MANUAL = re.compile(
    r"\bmanual\w*\b[^.]{0,80}\bdownstream\b|\bdownstream\b[^.]{0,80}\bmanual\w*\b",
    re.IGNORECASE,
)


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def defects(docs: dict) -> list:
    bad = []
    for name, raw in docs.items():
        text = collapse(raw)
        bad += [f"{name}: still says {frag!r}" for frag in STALE if frag in text]
        if not (PAUSED.search(text) and MANUAL.search(text)):
            bad.append(
                f"{name}: does not say the full scan is paused and conformance is verified manually, downstream"
            )
    return bad


def run(root: Path) -> int:
    missing = [s for s in SOURCES if not (root / s).exists()]
    if missing:
        print(f"RED: missing {missing}", file=sys.stderr)
        return 1
    bad = defects({s: (root / s).read_text(encoding="utf-8") for s in SOURCES})
    if bad:
        print("RED: " + "; ".join(bad), file=sys.stderr)
        return 1
    print(
        "OK: accessibility statements say the full scan is paused and verified manually, downstream"
    )
    return 0


def self_test() -> int:
    good = "The full-page scan is currently paused in CI. Conformance is verified manually, downstream."
    cases = [
        ("honest wording passes", good, 0),
        ("no acknowledgement fails", "Accessibility notes.", 1),
    ]
    for frag in STALE:
        cases.append(
            (
                f"stale wording fails: {frag[:40]}",
                good + "\nAn axe-core " + frag.replace("\n", " "),
                1,
            )
        )
    cases.append(
        (
            "stale wording wrapped across lines fails",
            good + "\n" + STALE[0].replace(" ", "\n", 3),
            1,
        )
    )
    failed = 0
    for label, doc, want in cases:
        got = 1 if defects({"fixture": doc}) else 0
        if got != want:
            print(
                f"self-test FAIL: {label} (expected {want}, got {got})", file=sys.stderr
            )
            failed += 1
    with tempfile.TemporaryDirectory() as tmp:
        if run(Path(tmp)) != 1:
            print("self-test FAIL: empty tree must be refused", file=sys.stderr)
            failed += 1
    print("self-test OK" if not failed else f"self-test: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else run(Path(".")))
