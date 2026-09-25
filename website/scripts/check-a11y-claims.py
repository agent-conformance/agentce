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

SKIP_RE = re.compile(r"AGENTCE_SKIP_A11Y\s*[:=]\s*[\"']?([^\s\"'#]*)")
SCAN = re.compile(r"\b(scan\w*|axe\S*|automated (rules engine|gate|checks?|tests?)|(a11y|accessibility) (gate|checks?|tests?))\b", re.IGNORECASE)
CI = re.compile(
    r"\b(ci|continuous integration|every (change|commit|push|pull request)|(every|all|each) (built |published )?pages?)\b",
    re.IGNORECASE,
)
HONEST = re.compile(
    r"\bpaused\b|\blast full run\b|\b(scan|scanning|gate)\b[^.]{0,30}\b(skipped|disabled)\b|\bscan\w* itself does not\b"
    r"|(until|once|when|if)\b[^.]{0,60}\bresum",
    re.IGNORECASE,
)
NEGATED = re.compile(r"\b(never|not|isn'?t|aren'?t|no longer)\s+(\w+\s+)?(paused|skipped|disabled)\b|\balways\b", re.IGNORECASE)
PAUSE_ACK = re.compile(r"(?<!not )(?<!never )\bpaused\b", re.IGNORECASE)
MANUAL_ACK = re.compile(r"\bmanual\w*\b[^.]{0,80}\bdownstream\b|\bdownstream\b[^.]{0,80}\bmanual\w*\b", re.IGNORECASE)


def scan_step_paused(workflow_text: str) -> bool:
    """True unless the a11y job visibly runs the full-page scan with no skip in effect."""
    m = re.search(r"^  a11y:\n(.*?)(?=^  \S|\Z)", workflow_text, re.MULTILINE | re.DOTALL)
    if not m:
        return True
    job = m.group(1)
    if any(v not in ("", "0") for v in SKIP_RE.findall(workflow_text)) or re.search(r"^    if:", job, re.MULTILINE):
        return True
    steps = re.split(r"^      - ", job, flags=re.MULTILINE)
    full = [st for st in steps if "check-a11y.mjs" in st and "--self-test" not in st]
    return not full or any(re.search(r"^\s+if:", st, re.MULTILINE) for st in full)


def sentences(raw: str):
    block: list = []
    for line in raw.split("\n") + [""]:
        starts_new = not line.strip() or re.match(r"\s*(#|\||[-*]\s|\d+\.\s)", line)
        if starts_new and block:
            yield from re.split(r"(?<=[.!?])\s+|\s+-\s+|\s*\|\s*", re.sub(r"\s+", " ", " ".join(block)))
            block = []
        if line.strip():
            if re.match(r"\s*#", line):
                yield re.sub(r"\s+", " ", line)
            else:
                block.append(line)


def defects(workflow_text: str, docs: dict) -> list:
    if not scan_step_paused(workflow_text):
        return []
    bad = []
    for name, raw in docs.items():
        sents = list(sentences(raw))
        for sentence in sents:
            if SCAN.search(sentence) and CI.search(sentence) and (not HONEST.search(sentence) or NEGATED.search(sentence)):
                bad.append(f"{name}: asserts the full scan runs in CI / on every page: {sentence[:100]!r}")
                break
        acked = any(PAUSE_ACK.search(x) and re.search(r"\bci\b|continuous integration", x, re.IGNORECASE) and not NEGATED.search(x) for x in sents)
        manual = any(MANUAL_ACK.search(x) and not NEGATED.search(x) for x in sents)
        if not (acked and manual):
            bad.append(f"{name}: does not say the full scan is paused in CI and verified manually, downstream")
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
    good = "The full-page scan is currently paused in CI. Conformance is verified manually, downstream."
    wf = "  a11y:\n    steps:\n      - name: Gate\n        env:\n          AGENTCE_SKIP_A11Y: \"1\"\n        run: node scripts/check-a11y.mjs\n"
    live = "  a11y:\n    steps:\n      - name: Gate\n        run: node scripts/check-a11y.mjs\n"
    stale_vpat = "| 1.1.1 Non-text Content | Not Evaluated | Automated axe-core WCAG 2.2 AA scan (every page, both themes, in CI) reports no violations for this rule; not yet manually verified. |"
    stale_vpat2 = "| 1.4.3 Contrast (Minimum) | Not Evaluated | Automated axe-core `color-contrast` rule reports no violations across every page and theme; not yet manually verified. |"
    stale_stmt = "An automated rules engine (axe-core) scans every published page\nin both the light and dark themes on every change and currently reports zero violations."
    stale_stmt2 = "It is checked by an automated accessibility gate that runs in\ncontinuous integration on every page, in both themes, on every change to this site."
    stale_protocol = "Automated scanning (axe-core, run in continuous integration on every page in both themes)."
    cases = [
        ("good passes", wf, good, 0),
        ("honest self-test wording passes", wf, good + " The gate's self-test still runs in CI on every change.", 0),
        ("honest past tense passes", wf, good + " Its last full run reported no violations across every page.", 0),
        ("honest conditional passes", wf, good + " Until the scan resumes in CI on every page, testing is manual.", 0),
        ("stale VPAT row fails", wf, good + "\n" + stale_vpat, 1),
        ("reworded VPAT row with an incidental 'does not' fails", wf, good + "\n| 1.1.1 Non-text Content | Not Evaluated | Automated axe-core WCAG 2.2 AA scan (every page, both themes, in CI) does not report violations for this rule. |", 1),
        ("'no page is skipped' fails", wf, good + " Axe scans every page in CI and no page is skipped.", 1),
        ("stale VPAT contrast row fails", wf, good + "\n" + stale_vpat2, 1),
        ("stale statement fails", wf, good + "\n\n" + stale_stmt, 1),
        ("stale statement gate sentence fails", wf, good + "\n\n" + stale_stmt2, 1),
        ("scan in CI with a manual-testing aside fails", wf, good + " Axe scans every page in CI; manual testing complements it.", 1),
        ("scan in CI mentioning its self-test fails", wf, good + " The scan runs in CI on every page and its self-test proves it has teeth.", 1),
        ("job-level if counts as paused", "  a11y:\n    if: false\n    steps:\n      - run: node scripts/check-a11y.mjs\n", "Axe runs in CI on every page.", 1),
        ("workflow-level skip counts as paused", "env:\n  AGENTCE_SKIP_A11Y: 1\njobs:\n  a11y:\n    steps:\n      - run: node scripts/check-a11y.mjs\n", "Axe runs in CI on every page.", 1),
        ("stale protocol wording fails", wf, good + "\n\n" + stale_protocol, 1),
        ("reordered claim fails", wf, good + " In CI, on every page, the scan is run.", 1),
        ("passive claim fails", wf, good + " Every page is scanned in CI.", 1),
        ("negated pause fails", wf, good + " The scan is never paused in CI on any page.", 1),
        ("negated acknowledgement fails", wf, "The scan is not paused in CI. Testing is manual, downstream.", 1),
        ("missing acknowledgement fails", wf, "Accessibility notes.", 1),
        ("acknowledgement without manual half fails", wf, "The scan is paused in CI.", 1),
        ("heading text does not bleed into a claim", wf, good + "\n\n## Scan\nEvery page is listed below.", 0),
        ("'false' value counts as paused", wf.replace('"1"', '"false"'), "Axe runs in CI on every page.", 1),
        ("inline skip counts as paused", "  a11y:\n    steps:\n      - run: AGENTCE_SKIP_A11Y=1 node scripts/check-a11y.mjs\n", "Axe runs in CI on every page.", 1),
        ("skip with a trailing comment counts as paused", wf.replace('"1"', '1 # pause'), "Axe runs in CI on every page.", 1),
        ("templated skip counts as paused", wf.replace('"1"', "${{ vars.SKIP }}"), "Axe runs in CI on every page.", 1),
        ("a conditional scan step counts as paused", "  a11y:\n    steps:\n      - name: Gate\n        if: false\n        run: node scripts/check-a11y.mjs\n", "Axe runs in CI on every page.", 1),
        ("a missing scan step counts as paused", "  a11y:\n    steps:\n      - run: node scripts/check-a11y.mjs --self-test\n", "Axe runs in CI on every page.", 1),
        ("a live scan step needs no acknowledgement", live, "Axe runs in CI on every page.", 0),
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
