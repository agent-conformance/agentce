#!/usr/bin/env python3
"""placeholder_sweep - scan tracked files for stub and placeholder markers (see ADR-0011).

The project's rule is that nothing ships as if it were finished when it is not: no TODO, stub,
"not yet implemented", zeroed digest, or empty section slips into the tree unnoticed. This scanner
walks every tracked file for a fixed set of placeholder patterns and fails on any hit that is not
accounted for in ``tools/placeholder_allowlist.yaml``.

Every allowlist entry carries a class and a justification:

* ``legitimate`` - a permanent, honest placeholder: a catalog entry labelled ``status: placeholder``,
  an illustrative example identifier, or a genesis all-zero hash-chain pointer in a fixture.
* ``debt`` - a known defect awaiting its fix, such as an engine command that is "not yet
  implemented". A debt entry is removed by the change that fixes it; the debt total may only fall,
  and the extended end-to-end gate requires it to reach zero (``--fail-on-debt``).

    placeholder_sweep.py                 scan the repository; exit 1 on any unaccounted hit or stale entry
    placeholder_sweep.py --report        print every current hit as allowlist entries (a triage aid)
    placeholder_sweep.py --fail-on-debt  additionally fail if any debt entry remains (the e2e gate)
    placeholder_sweep.py --self-test     prove the scanner discriminates over good and bad fixtures

Uses PyYAML (``safe_load`` only, per ADR-0005). No network, no learned component.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ALLOWLIST = HERE / "placeholder_allowlist.yaml"

# The scanner's own machinery necessarily names every pattern, so it cannot scan itself: its source,
# its allowlist, and its self-test fixtures are excluded from the repository scan. Paths are relative
# to the repository root; the fixtures are exercised only by --self-test.
SELF_EXCLUDE = (
    "tools/placeholder_sweep.py",
    "tools/placeholder_allowlist.yaml",
    "tools/placeholder/fixtures/",
)

_UUID_ZERO = r"\b0{8}-0{4}-0{4}-0{4}-0{12}\b"
_HEX_ZERO = r"(?<![0-9a-fA-F])0{32,}(?![0-9a-fA-F])"

# Stable pattern id -> compiled regex. Ordered for deterministic reporting. Case-insensitive unless a
# pattern is intrinsically case-sensitive. The empty-markdown-section pattern is structural and is
# handled separately (see _empty_md_sections); it shares the id "empty-md-section".
PATTERNS: dict[str, re.Pattern[str]] = {
    "todo": re.compile(r"\bTODO\b", re.I),
    "fixme": re.compile(r"\bFIXME\b", re.I),
    "xxx": re.compile(r"\bXXX\b", re.I),
    "tbd": re.compile(r"\bTBD\b", re.I),
    "hack": re.compile(r"\bHACK\b", re.I),
    "placeholder": re.compile(r"placeholder", re.I),
    "not-implemented": re.compile(
        r"not yet implemented|not implemented|unimplemented"
        r"|NotImplementedError|UnsupportedOperationException",
        re.I,
    ),
    "coming-soon": re.compile(r"coming soon", re.I),
    "under-construction": re.compile(r"under construction", re.I),
    "lorem-ipsum": re.compile(r"lorem ipsum", re.I),
    "stub": re.compile(r"\bstub\b", re.I),
    "dummy": re.compile(r"\bdummy\b", re.I),
    "changeme": re.compile(r"changeme", re.I),
    "zero-digest": re.compile(f"{_HEX_ZERO}|{_UUID_ZERO}"),
    "example-domain": re.compile(r"example\.(?:com|org)", re.I),
}

EMPTY_MD_SECTION = "empty-md-section"
ALL_PATTERN_IDS = (*PATTERNS.keys(), EMPTY_MD_SECTION)

# example.com / example.org are the reserved illustrative domains (RFC 2606). Inside fixtures,
# testdata, documentation, and *.template.* files they are expected, not placeholders (Appendix E),
# so the example-domain pattern does not fire there.
_EXAMPLE_DOMAIN_EXEMPT = re.compile(
    r"(^|/)(fixtures|testdata|tests?|docs)(/|$)|\.template\."
)

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")
_MD_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def _md_heading_level(line: str) -> int | None:
    m = _MD_HEADING.match(line)
    return len(m.group(1)) if m else None


def _empty_md_sections(text: str) -> int:
    """Count headings whose section is empty: the next non-blank line is a heading of the same or a
    higher level (a deeper heading is a legitimate subsection). Fenced code blocks are skipped so a
    ``#`` comment inside a fence is never read as a heading."""
    lines = text.splitlines()
    in_fence = False
    headings: list[tuple[int, int]] = []
    fence_at: set[int] = set()
    for i, line in enumerate(lines):
        if _MD_FENCE.match(line):
            in_fence = not in_fence
            fence_at.add(i)
            continue
        if in_fence:
            continue
        level = _md_heading_level(line)
        if level is not None:
            headings.append((i, level))

    def next_nonblank(after: int) -> int | None:
        j = after + 1
        while j < len(lines):
            if j not in fence_at and lines[j].strip():
                return j
            j += 1
        return None

    heading_lines = {i for i, _ in headings}
    count = 0
    for i, level in headings:
        j = next_nonblank(i)
        if j is not None and j in heading_lines:
            nxt = _md_heading_level(lines[j])
            if nxt is not None and nxt <= level:
                count += 1
    return count


def count_hits(rel_path: str, text: str) -> dict[str, int]:
    """Return {pattern_id: occurrence count} for one file's text (only patterns that fired)."""
    hits: dict[str, int] = {}
    exempt_example = bool(_EXAMPLE_DOMAIN_EXEMPT.search(rel_path))
    for pid, rx in PATTERNS.items():
        if pid == "example-domain" and exempt_example:
            continue
        n = len(rx.findall(text))
        if n:
            hits[pid] = n
    if rel_path.endswith(".md"):
        n = _empty_md_sections(text)
        if n:
            hits[EMPTY_MD_SECTION] = n
    return hits


def _read_text(path: Path) -> str | None:
    """Return the file's text, or None if it is binary/unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def list_tracked_files(root: Path) -> list[str]:
    """Repository-relative paths of tracked files, minus the scanner's own machinery, sorted."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=True,
    )
    files = [p for p in out.stdout.split("\0") if p]
    return sorted(p for p in files if not p.startswith(SELF_EXCLUDE))


# A fixture directory's own control files are not scanned content.
_FIXTURE_CONTROL = {"allowlist.yaml", "expect.txt"}


def walk_files(root: Path) -> list[str]:
    """Root-relative paths of every content file under root (used for self-test fixtures, which are
    not their own git repository), excluding a fixture's control files, sorted."""
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.name not in _FIXTURE_CONTROL
    )


def scan(root: Path, rel_paths: list[str]) -> dict[tuple[str, str], int]:
    """Return {(rel_path, pattern_id): count} for every hit across the given files."""
    hits: dict[tuple[str, str], int] = {}
    for rel in rel_paths:
        text = _read_text(root / rel)
        if text is None:
            continue
        for pid, n in count_hits(rel, text).items():
            hits[(rel, pid)] = n
    return hits


def load_allowlist(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse the allowlist, returning (entries, schema errors)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    if not path.exists():
        return [], [f"allowlist not found: {path}"]
    if not isinstance(data, dict) or not isinstance(data.get("allow"), list):
        return [], ["allowlist: top-level must be a mapping with an 'allow' list"]
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, entry in enumerate(data["allow"]):
        loc = f"allow[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{loc}: must be a mapping")
            continue
        path_v, pat, cls = entry.get("path"), entry.get("pattern"), entry.get("class")
        just, count = entry.get("justification"), entry.get("count")
        if not isinstance(path_v, str) or not path_v:
            errors.append(f"{loc}: missing 'path'")
        if pat not in ALL_PATTERN_IDS:
            errors.append(
                f"{loc}: 'pattern' must be one of {list(ALL_PATTERN_IDS)}, got {pat!r}"
            )
        if cls not in ("legitimate", "debt"):
            errors.append(f"{loc}: 'class' must be 'legitimate' or 'debt', got {cls!r}")
        if not isinstance(just, str) or not just.strip():
            errors.append(f"{loc}: missing 'justification'")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errors.append(f"{loc}: 'count' must be a positive integer, got {count!r}")
        entries.append(entry)
    return entries, errors


def reconcile(
    hits: dict[tuple[str, str], int], entries: list[dict[str, Any]]
) -> list[str]:
    """Compare hits against the allowlist. Every hit needs an entry with the exact count; every entry
    must match a current hit. Returns violations (empty means the tree is accounted for)."""
    violations: list[str] = []
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for e in entries:
        key = (e.get("path"), e.get("pattern"))
        if key in by_key:
            violations.append(f"allowlist: duplicate entry for {key[0]} / {key[1]}")
        by_key[key] = e

    for (path, pid), n in sorted(hits.items()):
        entry = by_key.get((path, pid))
        if entry is None:
            violations.append(
                f"{path}: {n} unaccounted '{pid}' hit(s) - fix them, or add an allowlist entry "
                f"with a class and justification"
            )
        elif entry.get("count") != n:
            violations.append(
                f"{path}: '{pid}' hit count is {n} but the allowlist expects {entry.get('count')} "
                f"- update tools/placeholder_allowlist.yaml"
            )
    for (path, pid), entry in sorted(by_key.items()):
        if (path, pid) not in hits:
            violations.append(
                f"allowlist: stale entry for {path} / {pid} matches nothing - remove it"
            )
    return violations


def find_root() -> Path:
    """The repository root: the parent of tools/, where the scanner module lives."""
    return HERE.parent


def run_check(fail_on_debt: bool = False) -> int:
    root = find_root()
    entries, errors = load_allowlist(ALLOWLIST)
    if errors:
        print("PLACEHOLDER SWEEP FAILED:")
        for e in errors:
            print(f"  {e}")
        return 1
    files = list_tracked_files(root)
    hits = scan(root, files)
    violations = reconcile(hits, entries)
    if violations:
        print("PLACEHOLDER SWEEP FAILED:")
        for v in violations:
            print(f"  {v}")
        return 1
    legitimate = sum(e["count"] for e in entries if e["class"] == "legitimate")
    debt = sum(e["count"] for e in entries if e["class"] == "debt")
    if fail_on_debt and debt:
        print(
            f"PLACEHOLDER SWEEP FAILED: {debt} debt hit(s) remain; the gate requires zero:"
        )
        for entry in entries:
            if entry["class"] == "debt":
                print(
                    f"  {entry['path']} / {entry['pattern']} (x{entry['count']}): "
                    f"{entry['justification']}"
                )
        return 1
    print(
        f"PLACEHOLDER SWEEP OK ({len(files)} files scanned; "
        f"{legitimate + debt} allowed hits: {legitimate} legitimate, {debt} debt)"
    )
    return 0


def report() -> int:
    """Print every current hit as allowlist-entry skeletons, to triage a fresh baseline."""
    root = find_root()
    hits = scan(root, list_tracked_files(root))
    print("# Hits found. Triage each into tools/placeholder_allowlist.yaml.")
    for (path, pid), n in sorted(hits.items()):
        print(f"  - path: {path}")
        print(f"    pattern: {pid}")
        print(f"    count: {n}")
        print("    class: legitimate  # or debt")
        print('    justification: "TODO"')
    print(f"# {len(hits)} (path, pattern) group(s).")
    return 0


def self_test() -> int:
    """Run the scanner over the good and bad fixtures and prove each bad fixture is caught."""
    fixtures = HERE / "placeholder" / "fixtures"
    failures: list[str] = []

    good = fixtures / "good"
    entries, errors = load_allowlist(good / "allowlist.yaml")
    hits = scan(good, walk_files(good))
    violations = errors + reconcile(hits, entries)
    if violations:
        failures.append(f"good: expected no violations, got {violations}")

    for bad in sorted(p for p in fixtures.glob("bad-*") if p.is_dir()):
        expect = (bad / "expect.txt").read_text(encoding="utf-8").strip()
        entries, errors = load_allowlist(bad / "allowlist.yaml")
        hits = scan(bad, walk_files(bad))
        violations = errors + reconcile(hits, entries)
        if not violations:
            failures.append(f"{bad.name}: expected a violation, got none")
        elif not any(expect.lower() in v.lower() for v in violations):
            failures.append(
                f"{bad.name}: no violation contained {expect!r}; got {violations}"
            )

    if failures:
        print("PLACEHOLDER SWEEP SELF-TEST FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    print("PLACEHOLDER SWEEP SELF-TEST PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan tracked files for placeholder markers."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--self-test", action="store_true", help="run the fixture discrimination test"
    )
    group.add_argument(
        "--report", action="store_true", help="print current hits as allowlist stubs"
    )
    parser.add_argument(
        "--fail-on-debt",
        action="store_true",
        help="fail if any debt entry remains (the e2e gate)",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.report:
        return report()
    return run_check(fail_on_debt=args.fail_on_debt)


if __name__ == "__main__":
    raise SystemExit(main())
