#!/usr/bin/env python3
"""Source-level accessibility-statement / VPAT / manual-test-protocol gate.

Scans committed website/ markdown/MDX/Astro source (no build needed) for the accessibility statement,
the VPAT, and the manual test protocol, and checks each carries the structural elements the finding
requires -- most importantly the VPAT's honesty rule: every criterion reads "Not Evaluated" until a
person runs the manual protocol, with the automated evidence recorded in Remarks. This is the CI-facing
implementation of the same checks the contract's own RED-capture commands validate inline; run it from
the repository root.

Usage:
  python3 website/scripts/check-a11y-docs.py               Scan website/ source; exit 1 on any defect.
  python3 website/scripts/check-a11y-docs.py --self-test    Prove each check can fail: a temp fixture
                                                             tree with all three documents complete must
                                                             pass; deleting one required element from
                                                             each must independently fail.
"""
import glob
import re
import sys
import tempfile
from pathlib import Path

PATTERNS = ["website/**/*.md", "website/**/*.mdx", "website/**/*.astro"]
EXCLUDED = ("/node_modules/", "/dist/", "/.astro/")


def candidates(root: Path):
    seen = set()
    for pat in PATTERNS:
        for p in glob.glob(str(root / pat), recursive=True):
            norm = p.replace("\\", "/")
            if any(x in norm for x in EXCLUDED):
                continue
            if p not in seen:
                seen.add(p)
                yield Path(p)


STATEMENT_ANCHOR = re.compile(
    r'^(#\s+.*accessibility statement|title:\s*[\'"]?[^\n]*accessibility statement)',
    re.IGNORECASE | re.MULTILINE,
)
STATEMENT_MARKERS = {
    "compliance status": re.compile(r"complian(t|ce)\s+status", re.IGNORECASE),
    "scope": re.compile(r"\bscope\b", re.IGNORECASE),
    "non-accessible content": re.compile(
        r"non-accessible content|content (that|which) is not accessible|not (yet |fully )?accessible content",
        re.IGNORECASE,
    ),
    "feedback / contact": re.compile(r"feedback|contact (us|information|form|email)|how to contact", re.IGNORECASE),
    "enforcement / preparation": re.compile(
        r"enforcement (procedure|body)|preparation of (this|the) (accessibility )?statement", re.IGNORECASE
    ),
}


def check_statement(root: Path):
    for path in candidates(root):
        try:
            t = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if STATEMENT_ANCHOR.search(t):
            missing = [name for name, pat in STATEMENT_MARKERS.items() if not pat.search(t)]
            if missing:
                return False, f"{path} exists but is missing required EU-model elements: {missing}"
            return True, f"{path} carries all required EU-model accessibility-statement elements"
    return False, "no accessibility-statement page found under website/"


def split_row(line):
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def find_tables(text):
    lines = text.splitlines()
    i = 0
    tables = []
    while i < len(lines) - 1:
        header_line = lines[i]
        sep_line = lines[i + 1]
        if "|" in header_line and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", sep_line):
            header = split_row(header_line)
            j = i + 2
            rows = []
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                row = split_row(lines[j])
                if len(row) == len(header):
                    rows.append(row)
                j += 1
            tables.append((header, rows))
            i = j
        else:
            i += 1
    return tables


CLAUSE_MARKERS = {
    "Clause 9 (Web)": re.compile(r"\bclause\s*9\b", re.IGNORECASE),
    "Clause 10 (Non-web documents)": re.compile(r"\bclause\s*10\b", re.IGNORECASE),
    "Clause 11 (Software)": re.compile(r"\bclause\s*11\b", re.IGNORECASE),
}
EN301549_RE = re.compile(r"en\s*301[\s-]*549", re.IGNORECASE)
MIN_ROWS = 20


def check_vpat(root: Path):
    vpat_files = []
    all_text = []
    for path in candidates(root):
        try:
            t = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if re.search(r"\bVPAT\b", t, re.IGNORECASE) and EN301549_RE.search(t):
            vpat_files.append(path)
            all_text.append(t)

    if not vpat_files:
        return False, "no VPAT/ACR document found under website/ referencing both 'VPAT' and 'EN 301 549'"

    combined = "\n".join(all_text)
    missing_clauses = [name for name, pat in CLAUSE_MARKERS.items() if not pat.search(combined)]
    if missing_clauses:
        return False, f"VPAT document(s) {vpat_files} do not reference all of EN 301 549 clauses 9/10/11: missing {missing_clauses}"

    levels = []
    remarks_texts = []
    for t in all_text:
        for header, rows in find_tables(t):
            lower_header = [h.lower() for h in header]
            level_idx = next((k for k, h in enumerate(lower_header) if "conformance level" in h or h == "level"), None)
            if level_idx is None:
                continue
            remarks_idx = next((k for k, h in enumerate(lower_header) if "remarks" in h), None)
            for row in rows:
                levels.append(row[level_idx])
                if remarks_idx is not None:
                    remarks_texts.append(row[remarks_idx])

    if len(levels) < MIN_ROWS:
        return False, f"only {len(levels)} VPAT criteria rows found (need >= {MIN_ROWS})"

    bad = [lv for lv in levels if lv.strip().casefold() != "not evaluated"]
    if bad:
        return False, f"{len(bad)}/{len(levels)} VPAT criteria do not read 'Not Evaluated': {bad[:5]!r}"

    evidence_re = re.compile(r"axe|automated|check-a11y|a11y check", re.IGNORECASE)
    if not any(evidence_re.search(r) for r in remarks_texts):
        return False, "no VPAT remarks entry references the automated accessibility evidence"

    return True, f"{vpat_files} map EN 301 549 clauses 9/10/11, {len(levels)} rows all 'Not Evaluated', evidence in remarks"


PROTOCOL_ANCHOR = re.compile(
    r'^(#\s+.*(manual([- ]test)? (accessibility )?protocol|accessibility test protocol)'
    r'|title:\s*[\'"]?[^\n]*(manual([- ]test)? (accessibility )?protocol|accessibility test protocol))',
    re.IGNORECASE | re.MULTILINE,
)


def _heading_re(pat):
    return re.compile(rf"^#{{1,6}}\s+.*{pat}", re.IGNORECASE | re.MULTILINE)


PROTOCOL_SECTIONS = {
    "screen reader": _heading_re(r"screen\s*reader"),
    "keyboard": _heading_re(r"keyboard"),
    "zoom / magnification (200-400%)": _heading_re(r"zoom|magnif"),
    "forced-colors": _heading_re(r"forced[- ]colou?rs|high[- ]contrast mode"),
}


def check_protocol(root: Path):
    for path in candidates(root):
        try:
            t = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if PROTOCOL_ANCHOR.search(t):
            missing = [name for name, pat in PROTOCOL_SECTIONS.items() if not pat.search(t)]
            if "zoom / magnification (200-400%)" not in missing:
                if not (re.search(r"200\s*%", t) and re.search(r"400\s*%", t)):
                    missing.append("zoom / magnification (200-400%) [range 200%/400% not both present]")
            if missing:
                return False, f"{path} exists but is missing required manual-test sections: {missing}"
            return True, f"{path} covers all required manual-test methods"
    return False, "no manual accessibility test protocol document found under website/"


CHECKS = [("accessibility statement", check_statement), ("VPAT", check_vpat), ("manual test protocol", check_protocol)]


def run(root: Path):
    problems = []
    for name, fn in CHECKS:
        ok, msg = fn(root)
        print(f"{'GREEN' if ok else 'RED'}: {name}: {msg}")
        if not ok:
            problems.append(f"{name}: {msg}")
    return problems


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good"
        _write(
            good / "website/src/content/docs/docs/accessibility-statement.md",
            "---\ntitle: Accessibility Statement\n---\nCompliance status: partial. Scope: this site. "
            "Non-accessible content: none known. Feedback and contact: email us. Enforcement procedure "
            "and preparation of this statement: see above.\n",
        )
        rows = "\n".join(f"| C{i} | Not Evaluated | axe automated evidence |" for i in range(22))
        _write(
            good / "website/src/content/docs/docs/vpat.md",
            "---\ntitle: VPAT\n---\nEN 301 549 Clause 9 Clause 10 Clause 11.\n\n"
            f"| Criteria | Conformance Level | Remarks |\n|---|---|---|\n{rows}\n",
        )
        _write(
            good / "website/src/content/docs/docs/accessibility-testing-protocol.md",
            "---\ntitle: Accessibility Test Protocol\n---\n## Screen reader\n## Keyboard\n"
            "## Zoom (200%-400%)\n## Forced-colors\n",
        )
        problems = run(good)
        if problems:
            print("SELF-TEST FAILED: expected the good fixture to pass, got:", problems, file=sys.stderr)
            return 1

        # Bad: delete the statement's feedback/contact section.
        bad_statement = root / "bad-statement"
        _write(
            bad_statement / "website/src/content/docs/docs/accessibility-statement.md",
            "---\ntitle: Accessibility Statement\n---\nCompliance status: partial. Scope: this site. "
            "Non-accessible content: none known. Enforcement procedure and preparation of this "
            "statement: see above.\n",
        )
        _write(bad_statement / "website/src/content/docs/docs/vpat.md", (good / "website/src/content/docs/docs/vpat.md").read_text())
        _write(
            bad_statement / "website/src/content/docs/docs/accessibility-testing-protocol.md",
            (good / "website/src/content/docs/docs/accessibility-testing-protocol.md").read_text(),
        )
        ok, msg = check_statement(bad_statement)
        if ok or "feedback" not in str(msg).lower():
            print("SELF-TEST FAILED: expected a missing-feedback statement failure, got:", ok, msg, file=sys.stderr)
            return 1

        # Bad: one VPAT row flips to "Supports".
        bad_vpat_rows = "\n".join(
            f"| C{i} | {'Supports' if i == 0 else 'Not Evaluated'} | axe automated evidence |" for i in range(22)
        )
        bad_vpat = root / "bad-vpat"
        _write(
            bad_vpat / "website/src/content/docs/docs/vpat.md",
            "---\ntitle: VPAT\n---\nEN 301 549 Clause 9 Clause 10 Clause 11.\n\n"
            f"| Criteria | Conformance Level | Remarks |\n|---|---|---|\n{bad_vpat_rows}\n",
        )
        ok, msg = check_vpat(bad_vpat)
        if ok or "not read" not in str(msg).lower():
            print("SELF-TEST FAILED: expected an honesty-rule VPAT failure, got:", ok, msg, file=sys.stderr)
            return 1

        # Bad: protocol drops the forced-colors section.
        bad_protocol = root / "bad-protocol"
        _write(
            bad_protocol / "website/src/content/docs/docs/accessibility-testing-protocol.md",
            "---\ntitle: Accessibility Test Protocol\n---\n## Screen reader\n## Keyboard\n## Zoom (200%-400%)\n",
        )
        ok, msg = check_protocol(bad_protocol)
        if ok or "forced-colors" not in str(msg):
            print("SELF-TEST FAILED: expected a missing-forced-colors protocol failure, got:", ok, msg, file=sys.stderr)
            return 1

        print("SELF-TEST OK — each check passes a complete fixture and independently fails one missing a required element.")
        return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    root = Path.cwd()
    problems = run(root)
    if problems:
        print("A11Y-DOCS CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("A11Y-DOCS CHECK OK — the accessibility statement, VPAT, and manual test protocol are all present and complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
