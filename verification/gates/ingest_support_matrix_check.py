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

_KNOWN_TIERS = ("Supported", "Experimental", "Roadmap")
_TIER_LINE_RE = re.compile(
    r"^\*\*(" + "|".join(_KNOWN_TIERS) + r")\*\* \((\d+)\): (.+)$", re.MULTILINE
)
_MORE_RE = re.compile(r"^and (\d+) more$")
_TRAILER_RE = re.compile(r"\s*—\s*(?:see the full matrix|help wanted)\s*$")
_TABLE_HEADER = "| Backend | Tier | Reader follows | Build item |"
_FOLLOWS_RE = re.compile(r"\]\(([^)]+)\),\s*(.+)$")


def _extract_table_rows(text: str) -> list[list[str]]:
    """Every row of the trace-store table, as its raw cells, with no shape assumed.

    Locates the table by its fixed header (skipping the header and its ``---`` separator), then
    takes every following non-blank line containing a ``|`` up to the first blank line, splitting
    on ``|`` rather than matching a regex against a presumed-good shape. GFM renders a table row
    whether or not it carries its outer pipes, so a leading or trailing ``|`` is stripped only when
    present, never required — a row missing one (or both) still reaches the caller as a row, the
    same as a row with an unexpected column count or an unrecognized tier word, instead of silently
    falling out of scrutiny the way a row lacking both outer pipes used to.
    """
    lines = text.splitlines()
    try:
        start = lines.index(_TABLE_HEADER) + 2
    except ValueError:
        return []
    rows: list[list[str]] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or "|" not in stripped:
            break
        cells = stripped.split("|")
        if stripped.startswith("|"):
            cells = cells[1:]
        if stripped.endswith("|"):
            cells = cells[:-1]
        rows.append([cell.strip() for cell in cells])
    return rows


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
    for tier in _KNOWN_TIERS:
        if tier.lower() not in seen_tiers:
            problems.append(
                f"ingest.matrix.missing_tier_line: {source} has no {tier} line"
            )
    return problems


def check_trace_store_docs(rows_by_name: dict[str, dict], text: str) -> list[str]:
    problems: list[str] = []
    rows_seen = 0
    for cells in _extract_table_rows(text):
        if len(cells) != 4:
            problems.append(
                f"ingest.matrix.docs_malformed_row: trace-store-connectors.md's table has a row "
                f"with {len(cells)} column(s), not 4: {cells!r}"
            )
            continue
        rows_seen += 1
        name = cells[0].strip("*").strip()
        tier_text = cells[1].strip("*").strip()
        follows = cells[2]
        if tier_text not in _KNOWN_TIERS:
            problems.append(
                f"ingest.matrix.docs_unrecognized_tier: trace-store-connectors.md names {name!r} "
                f"with tier {tier_text!r}, not one of {', '.join(_KNOWN_TIERS)}"
            )
            continue
        row = rows_by_name.get(name)
        if row is None:
            problems.append(
                f"ingest.matrix.docs_unknown_source: trace-store-connectors.md's table names "
                f"{name!r}, which is not a matrix row"
            )
            continue
        if row["tier"] != tier_text.lower():
            problems.append(
                f"ingest.matrix.docs_wrong_tier: trace-store-connectors.md's table says "
                f"{name!r} is {tier_text}, but the matrix grades it {row['tier']}"
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
            f"only {rows_seen} well-formed row(s) (expected at least 4: Langfuse, Phoenix, Datadog "
            "LLM Observability, LangSmith)"
        )
    return problems


def check(root: Path | None = None) -> list[str]:
    """Run the real check. ``root`` defaults to the repository root; pass a different root (for
    example a temporary copy with one file mutated) to check that copy instead."""
    base = root if root is not None else ROOT
    matrix = base / "spec/ingest/support-matrix.yaml"
    readme = base / "README.md"
    trace_store_docs = base / "website/src/content/docs/docs/trace-store-connectors.md"
    rows_by_name = load_matrix_rows(matrix)
    problems = check_tier_lines(
        rows_by_name, readme.read_text(encoding="utf-8"), "README.md"
    )
    problems += check_trace_store_docs(
        rows_by_name, trace_store_docs.read_text(encoding="utf-8")
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
    unknown_problems = check_tier_lines(rows, unknown, "README.md")
    cases.append(
        (
            "a name outside the matrix is unknown_source, not wrong_tier",
            any(
                "ingest.matrix.unknown_source" in p and "Zephyr" in p
                for p in unknown_problems
            )
            and not any("wrong_tier" in p for p in unknown_problems),
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

    # The verifier's adversarial probes (18.39 verifier round 1): a row shaped differently from
    # the four well-formed ones must still be scrutinised, never silently dropped because it did
    # not match an assumed regex shape.
    too_few_cells_table = good_table + "| Honeycomb | GA |\n"
    cases.append(
        (
            "a 2-column row is docs_malformed_row, not silently dropped",
            any(
                "ingest.matrix.docs_malformed_row" in p and "Honeycomb" in p
                for p in check_trace_store_docs(rows, too_few_cells_table)
            ),
        )
    )

    three_cell_table = good_table + "| Honeycomb | Supported | x |\n"
    cases.append(
        (
            "a 3-column row is docs_malformed_row, not silently dropped",
            any(
                "ingest.matrix.docs_malformed_row" in p
                for p in check_trace_store_docs(rows, three_cell_table)
            ),
        )
    )

    bold_tier_overclaim_table = good_table.replace(
        "| Datadog LLM Observability | Experimental | x | y |\n",
        "| Datadog LLM Observability | **Supported** | x | y |\n",
    )
    cases.append(
        (
            "a bold-markdown tier cell is read through the markup, not treated as an unrecognised "
            "shape that lets the Supported overclaim through",
            any(
                "ingest.matrix.docs_wrong_tier" in p and "Datadog" in p
                for p in check_trace_store_docs(rows, bold_tier_overclaim_table)
            ),
        )
    )

    unrecognized_tier_table = good_table.replace(
        "| Datadog LLM Observability | Experimental | x | y |\n",
        "| Datadog LLM Observability | GA | x | y |\n",
    )
    cases.append(
        (
            "a tier word outside Supported/Experimental/Roadmap is docs_unrecognized_tier, not dropped",
            any(
                "ingest.matrix.docs_unrecognized_tier" in p
                and "Datadog" in p
                and "'GA'" in p
                for p in check_trace_store_docs(rows, unrecognized_tier_table)
            ),
        )
    )

    # The verifier's adversarial probes (18.39 verifier round 2): GFM renders a table row whether
    # or not it carries its outer pipes, so a row missing one or both must still be scrutinised.
    no_leading_pipe_table = good_table + "Honeycomb | Supported | x | y |\n"
    cases.append(
        (
            "a row missing its leading pipe is still read as a row, not dropped",
            any(
                "ingest.matrix.docs_unknown_source" in p and "Honeycomb" in p
                for p in check_trace_store_docs(rows, no_leading_pipe_table)
            ),
        )
    )

    no_trailing_pipe_table = good_table + "| Honeycomb | Supported | x | y\n"
    cases.append(
        (
            "a row missing its trailing pipe is still read as a row, not dropped",
            any(
                "ingest.matrix.docs_unknown_source" in p and "Honeycomb" in p
                for p in check_trace_store_docs(rows, no_trailing_pipe_table)
            ),
        )
    )

    no_outer_pipes_table = good_table + "Honeycomb | Supported | x | y\n"
    cases.append(
        (
            "a row missing both outer pipes is still read as a row, not dropped",
            any(
                "ingest.matrix.docs_unknown_source" in p and "Honeycomb" in p
                for p in check_trace_store_docs(rows, no_outer_pipes_table)
            ),
        )
    )

    duplicate_overclaim_no_pipe_table = good_table + (
        "Datadog LLM Observability | Supported | x | y\n"
    )
    cases.append(
        (
            "a second, pipe-less Datadog row claiming Supported is still caught as docs_wrong_tier",
            any(
                "ingest.matrix.docs_wrong_tier" in p and "Datadog" in p
                for p in check_trace_store_docs(rows, duplicate_overclaim_no_pipe_table)
            ),
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
