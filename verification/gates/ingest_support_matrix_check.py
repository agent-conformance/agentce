"""Driver for VG-INGEST-SUPPORT-MATRIX (18.39): README.md and the trace-store connectors docs page
name ingest sources only from spec/ingest/support-matrix.yaml, under the tier the matrix grades them.

Scans two copy surfaces, both of which make per-source tier claims today: README.md's three
``**<Tier>** (<count>): <names...>`` lines (one per tier; the Supported line may truncate with a
trailing ``and N more``), and the per-backend table in
``website/src/content/docs/docs/trace-store-connectors.md``. A name absent from the matrix, a name
under the wrong tier heading, a stated count that disagrees with the matrix, or a truncated line
whose own arithmetic does not add up to its stated count, each fails with its own message key so the
reason is never guessed from a generic diff.

    ingest_support_matrix_check.py              run the real check against the committed files
    ingest_support_matrix_check.py --self-test   prove each failure mode is caught, against synthetic
                                                  fixtures only (never the committed files)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX = ROOT / "spec" / "ingest" / "support-matrix.yaml"
README = ROOT / "README.md"
TRACE_STORE_DOCS = (
    ROOT / "website" / "src" / "content" / "docs" / "docs" / "trace-store-connectors.md"
)

_TIER_LINE_RE = re.compile(
    r"^\*\*(Supported|Experimental|Roadmap)\*\* \((\d+)\): (.+)$", re.MULTILINE
)
_MORE_RE = re.compile(r"^and (\d+) more$")
_TRAILER_RE = re.compile(r"\s*—\s*(?:see the full matrix|help wanted)\s*$")
_TABLE_ROW_RE = re.compile(
    r"^\| ([^|]+?) \| (Supported|Experimental|Roadmap) \| ([^|]+?) \| [^|]+? \|$",
    re.MULTILINE,
)
_FOLLOWS_RE = re.compile(r"\]\(([^)]+)\),\s*(.+)$")


def load_matrix_rows(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = {r["name"]: r for r in data["rows"]}
    if len(rows) != len(data["rows"]):
        raise ValueError("ingest.matrix.duplicate_name: two matrix rows share one name")
    return rows


def parse_tier_line(names_text: str) -> tuple[list[str], int | None]:
    """``names_text`` with any trailing period, "— see the full matrix"/"— help wanted" clause and
    "and N more" tail removed; returns the explicit names and that tail's N (None if absent)."""
    text = names_text.rstrip(".")
    text = _TRAILER_RE.sub("", text).rstrip(".")
    tokens = [t.strip() for t in text.split(",") if t.strip()]
    more = None
    if tokens:
        m = _MORE_RE.match(tokens[-1])
        if m:
            more = int(m.group(1))
            tokens = tokens[:-1]
    return tokens, more


def check_tier_lines(
    rows_by_name: dict[str, dict], text: str, source: str
) -> list[str]:
    problems: list[str] = []
    seen_tiers: set[str] = set()
    for tier_label, count_s, names_text in _TIER_LINE_RE.findall(text):
        tier = tier_label.lower()
        seen_tiers.add(tier)
        stated = int(count_s)
        real = sum(1 for r in rows_by_name.values() if r["tier"] == tier)
        if stated != real:
            problems.append(
                f"ingest.matrix.count_mismatch: {source} says {tier_label} ({stated}) but the "
                f"matrix has {real} {tier} row(s)"
            )
        names, more = parse_tier_line(names_text)
        if len(names) + (more or 0) != stated:
            problems.append(
                f"ingest.matrix.count_arithmetic: {source}'s {tier_label} line names "
                f"{len(names)} source(s) plus {more or 0} more, which does not add up to its "
                f"stated ({stated})"
            )
        for name in names:
            row = rows_by_name.get(name)
            if row is None:
                problems.append(
                    f"ingest.matrix.unknown_source: {source} names {name!r} under {tier_label}, "
                    "which is not a matrix row"
                )
            elif row["tier"] != tier:
                problems.append(
                    f"ingest.matrix.wrong_tier: {source} names {name!r} under {tier_label}, but "
                    f"the matrix grades it {row['tier']}"
                )
    for tier in ("Supported", "Experimental", "Roadmap"):
        if tier.lower() not in seen_tiers:
            problems.append(
                f"ingest.matrix.missing_tier_line: {source} has no {tier} line"
            )
    return problems


def check_trace_store_docs(rows_by_name: dict[str, dict], text: str) -> list[str]:
    problems: list[str] = []
    rows_seen = 0
    for name, tier_label, follows in _TABLE_ROW_RE.findall(text):
        name = name.strip()
        rows_seen += 1
        row = rows_by_name.get(name)
        if row is None:
            problems.append(
                f"ingest.matrix.docs_unknown_source: trace-store-connectors.md's table names "
                f"{name!r}, which is not a matrix row"
            )
            continue
        if row["tier"] != tier_label.lower():
            problems.append(
                f"ingest.matrix.docs_wrong_tier: trace-store-connectors.md's table says "
                f"{name!r} is {tier_label}, but the matrix grades it {row['tier']}"
            )
        m = _FOLLOWS_RE.search(follows.strip())
        definition = row.get("definition") or {}
        if m and definition.get("url") and definition.get("version"):
            doc_url, doc_version = m.group(1).strip(), m.group(2).strip()
            if doc_url != definition["url"]:
                problems.append(
                    f"ingest.matrix.docs_definition_drift: trace-store-connectors.md's table "
                    f"links {name!r} to {doc_url!r}, but the matrix's definition.url is "
                    f"{definition['url']!r}"
                )
            if doc_version != definition["version"]:
                problems.append(
                    f"ingest.matrix.docs_definition_drift: trace-store-connectors.md's table "
                    f"gives {name!r} version {doc_version!r}, but the matrix's definition.version "
                    f"is {definition['version']!r}"
                )
    if rows_seen < 4:
        problems.append(
            f"ingest.matrix.docs_table_missing: trace-store-connectors.md's backend table has "
            f"only {rows_seen} row(s) (expected at least 4: Langfuse, Phoenix, Datadog LLM "
            "Observability, LangSmith)"
        )
    return problems


def check(root: Path = ROOT) -> list[str]:
    rows_by_name = load_matrix_rows(root / "spec" / "ingest" / "support-matrix.yaml")
    problems = check_tier_lines(
        rows_by_name, (root / "README.md").read_text(encoding="utf-8"), "README.md"
    )
    problems += check_trace_store_docs(
        rows_by_name,
        (
            root
            / "website"
            / "src"
            / "content"
            / "docs"
            / "docs"
            / "trace-store-connectors.md"
        ).read_text(encoding="utf-8"),
    )
    return problems


def self_test() -> int:
    rows: dict[str, dict] = {
        "Claude Code session files": {"tier": "supported"},
        "OpenTelemetry GenAI traces": {"tier": "supported"},
        "Cursor CLI output": {"tier": "experimental"},
        "AuthZEN decisions": {"tier": "experimental"},
        "Datadog LLM Observability": {
            "tier": "experimental",
            "definition": {
                "url": "https://example.com/datadog-openapi",
                "version": "client 2.61.0",
            },
        },
        "Cursor": {"tier": "roadmap"},
        "Cedar": {"tier": "roadmap"},
    }
    cases: list[tuple[str, bool]] = []

    good_readme = (
        "**Supported** (2): Claude Code session files, OpenTelemetry GenAI traces.\n"
        "**Experimental** (3): Cursor CLI output, AuthZEN decisions, Datadog LLM Observability.\n"
        "**Roadmap** (2): Cursor, Cedar — help wanted.\n"
    )
    cases.append(
        (
            "a fully consistent README reports nothing",
            check_tier_lines(rows, good_readme, "README.md") == [],
        )
    )

    truncated = "**Supported** (2): Claude Code session files, and 1 more — see the full matrix.\n"
    cases.append(
        (
            "a truncated line whose arithmetic agrees reports nothing for that line",
            not any(
                "count_arithmetic" in p
                for p in check_tier_lines(rows, truncated, "README.md")
            ),
        )
    )

    bad_arithmetic = "**Supported** (2): Claude Code session files, and 5 more — see the full matrix.\n"
    cases.append(
        (
            "a truncated line whose arithmetic disagrees is caught",
            any(
                "ingest.matrix.count_arithmetic" in p
                for p in check_tier_lines(rows, bad_arithmetic, "README.md")
            ),
        )
    )

    unknown = "**Experimental** (3): Cursor CLI output, AuthZEN decisions, Zephyr.\n"
    cases.append(
        (
            "a name outside the matrix is unknown_source, not wrong_tier",
            any(
                "ingest.matrix.unknown_source" in p and "Zephyr" in p
                for p in check_tier_lines(rows, unknown, "README.md")
            )
            and not any(
                "wrong_tier" in p for p in check_tier_lines(rows, unknown, "README.md")
            ),
        )
    )

    # The swap fault: AuthZEN decisions (really experimental) moved under Roadmap, Cedar (really
    # roadmap) moved under Experimental. Both lines keep their real-world count, so only the
    # per-name tier check may fire -- count_mismatch and count_arithmetic must both stay silent.
    swapped_experimental = (
        "**Experimental** (3): Cursor CLI output, Cedar, Datadog LLM Observability.\n"
    )
    swapped_roadmap = "**Roadmap** (2): Cursor, AuthZEN decisions — help wanted.\n"
    swap_problems = check_tier_lines(
        rows, swapped_experimental, "README.md"
    ) + check_tier_lines(rows, swapped_roadmap, "README.md")
    cases.append(
        (
            "the isolated wrong-tier swap is caught by the tier rule alone, not by a count rule",
            sum("ingest.matrix.wrong_tier" in p for p in swap_problems) == 2
            and not any(
                "count_mismatch" in p or "count_arithmetic" in p for p in swap_problems
            ),
        )
    )

    cases.append(
        (
            "a stated count that disagrees with the real matrix is count_mismatch",
            any(
                "ingest.matrix.count_mismatch" in p
                for p in check_tier_lines(
                    rows,
                    "**Supported** (99): Claude Code session files, OpenTelemetry GenAI traces.\n",
                    "README.md",
                )
            ),
        )
    )

    missing_line = (
        "**Supported** (2): Claude Code session files, OpenTelemetry GenAI traces.\n"
    )
    cases.append(
        (
            "a missing tier line is reported by name",
            any(
                "missing_tier_line" in p and "Experimental" in p
                for p in check_tier_lines(rows, missing_line, "README.md")
            ),
        )
    )

    # "Cursor" (roadmap) is a substring of "Cursor CLI output" (experimental): exact name matching
    # must not conflate them. (Each snippet here names only one tier's line, so missing_tier_line
    # noise for the other two tiers is filtered out; that check has its own case below.)
    def _without_missing_tier(problems: list[str]) -> list[str]:
        return [p for p in problems if "missing_tier_line" not in p]

    cases.append(
        (
            "a bare roadmap name is not confused with the longer experimental name containing it",
            _without_missing_tier(
                check_tier_lines(
                    rows, "**Roadmap** (2): Cursor, Cedar — help wanted.\n", "README.md"
                )
            )
            == [],
        )
    )
    cases.append(
        (
            "the same bare name under the wrong heading is still caught as wrong_tier, by name",
            _without_missing_tier(
                check_tier_lines(
                    rows,
                    "**Experimental** (3): Cursor CLI output, AuthZEN decisions, Cursor.\n",
                    "README.md",
                )
            )
            == [
                "ingest.matrix.wrong_tier: README.md names 'Cursor' under Experimental, but the matrix grades it roadmap"
            ],
        )
    )

    good_table = (
        "| Backend | Tier | Reader follows | Build item |\n"
        "|---|---|---|---|\n"
        "| Cursor CLI output | Experimental | x | y |\n"
        "| AuthZEN decisions | Experimental | x | y |\n"
        "| Datadog LLM Observability | Experimental | x | y |\n"
        "| Claude Code session files | Supported | x | y |\n"
    )
    cases.append(
        (
            "a consistent docs table reports nothing",
            check_trace_store_docs(rows, good_table) == [],
        )
    )

    bad_table = good_table.replace(
        "Datadog LLM Observability | Experimental",
        "Datadog LLM Observability | Supported",
    )
    cases.append(
        (
            "the docs table's old Datadog claim (Supported, really Experimental) is caught",
            any(
                "ingest.matrix.docs_wrong_tier" in p and "Datadog" in p
                for p in check_trace_store_docs(rows, bad_table)
            ),
        )
    )

    short_table = "| Backend | Tier | Reader follows | Build item |\n|---|---|---|---|\n| Cursor CLI output | Experimental | x | y |\n"
    cases.append(
        (
            "a table naming too few matrix rows is docs_table_missing",
            any(
                "docs_table_missing" in p
                for p in check_trace_store_docs(rows, short_table)
            ),
        )
    )

    unknown_table = good_table.replace(
        "| Datadog LLM Observability | Experimental | x | y |\n",
        "| Honeycomb | Supported | x | y |\n",
    )
    cases.append(
        (
            "a table row naming a source outside the matrix is docs_unknown_source, not silently skipped",
            any(
                "ingest.matrix.docs_unknown_source" in p and "Honeycomb" in p
                for p in check_trace_store_docs(rows, unknown_table)
            ),
        )
    )

    drift_version_table = good_table.replace(
        "| Datadog LLM Observability | Experimental | x | y |\n",
        "| Datadog LLM Observability | Experimental | [x](https://example.com/datadog-openapi), client 9.9.9 | y |\n",
    )
    cases.append(
        (
            "a table row whose version disagrees with the matrix's definition.version is docs_definition_drift",
            any(
                "ingest.matrix.docs_definition_drift" in p and "9.9.9" in p
                for p in check_trace_store_docs(rows, drift_version_table)
            ),
        )
    )

    drift_url_table = good_table.replace(
        "| Datadog LLM Observability | Experimental | x | y |\n",
        "| Datadog LLM Observability | Experimental | [x](https://wrong.example/schema), client 2.61.0 | y |\n",
    )
    cases.append(
        (
            "a table row whose link disagrees with the matrix's definition.url is docs_definition_drift",
            any(
                "ingest.matrix.docs_definition_drift" in p and "wrong.example" in p
                for p in check_trace_store_docs(rows, drift_url_table)
            ),
        )
    )

    agreeing_table = good_table.replace(
        "| Datadog LLM Observability | Experimental | x | y |\n",
        "| Datadog LLM Observability | Experimental | [x](https://example.com/datadog-openapi), client 2.61.0 | y |\n",
    )
    cases.append(
        (
            "a table row whose link and version both agree with the matrix reports nothing",
            check_trace_store_docs(rows, agreeing_table) == [],
        )
    )

    failed = [name for name, ok in cases if not ok]
    if failed:
        for name in failed:
            print(f"ingest-support-matrix self-test FAIL: {name}", file=sys.stderr)
        return 1
    print(f"ingest-support-matrix self-test: {len(cases)}/{len(cases)} cases OK")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    problems = check()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"ingest-support-matrix: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(
        "ingest-support-matrix: README.md and trace-store-connectors.md agree with the matrix"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
