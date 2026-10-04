"""Driver for VG-REACH-TABLE (18.41): the reach headline and table -- on the website's Supported
sources page, in a short proof-strip item on the landing page, and in README.md -- carry the real
counts, dates and sources computed from spec/ingest/support-matrix.yaml's producers/producers_meta
data, never a hand-typed value that can drift from it.

The gate computes the canonical reach numbers from the matrix (VALUE-PROP.md "Reach" is the private
source of the same figures; this file is the public one everything renders from, per R8 "support
claims come only from the ingest support matrix"), then scans three public surfaces for the same
counts, check dates and source links: README.md's "## Reach: one reader per standard" table,
docs/reference/ingest-support-matrix.md's (and the published website copy's) copy of the same
table, and website/src/pages/index.astro's proof-strip claim. A surface whose stated number
disagrees with the computed one fails with `reach.table.count_mismatch`; a row missing its check
date or source link fails with `reach.table.missing_provenance`; a surface missing the section
entirely fails with `reach.table.missing_section`; a surface with the heading or a row duplicated
fails with `reach.table.duplicate_section`; a surface missing the locked headline sentence fails with
`reach.table.missing_headline`; a computed number that has fallen below the locked headline's floor
(400 tools, 600 cloud services, 25,000 MCP servers) fails with `reach.table.floor_violated` --
independently of what any surface currently states, so an honest but stale headline cannot pass
merely because every surface agrees with every other surface.

    python3 verification/gates/reach_table_check.py              # the real check
    python3 verification/gates/reach_table_check.py --self-test  # proves discrimination
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "spec" / "ingest" / "support-matrix.yaml"
README_PATH = REPO_ROOT / "README.md"
DOCS_PAGE_PATH = REPO_ROOT / "docs" / "reference" / "ingest-support-matrix.md"
SITE_PAGE_PATH = (
    REPO_ROOT
    / "website"
    / "src"
    / "content"
    / "docs"
    / "reference"
    / "ingest-support-matrix.md"
)
LANDING_PAGE_PATH = REPO_ROOT / "website" / "src" / "pages" / "index.astro"

TOOLS_FLOOR = 400
CLOUD_FLOOR = 600
MCP_FLOOR = 25_000

HEADING = "## Reach: one reader per standard"

HEADLINE = (
    "Reads records from 400+ tools and 600+ cloud services, and the tool declarations of "
    "25,000+ MCP servers, through the open standards they already write. One reader per "
    "standard, not one integration per product."
)


def load_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text("utf-8"))


def reach_numbers(matrix: dict[str, Any]) -> dict[str, int]:
    """The canonical counts, computed from the matrix's producers/producers_meta -- never typed.

    ``tools_total`` (the headline's "400+") sums ``agents.dedup.strict_total`` (OpenTelemetry GenAI
    + OpenInference, deduplicated) and ``security.dedup.total`` (every Kubernetes-certified
    distribution, including managed ones). ``tools_total_strict`` is the same sum using
    ``security.dedup.total_strict`` (self-managed Kubernetes distributions only, a stricter basis);
    it is shown parenthetically in the table, never as the headline number.
    """
    producers = matrix["producers"]
    meta = matrix["producers_meta"]
    agents_total = meta["agents"]["dedup"]["strict_total"]
    security_total = meta["security"]["dedup"]["total"]
    security_total_strict = meta["security"]["dedup"]["total_strict"]
    cloud_by_id = {c["id"]: c["count"] for c in meta["security"]["cloud_services"]}
    mcp = meta["agents"]["mcp"]
    return {
        "agents_total": agents_total,
        "security_total": security_total,
        "tools_total": agents_total + security_total,
        "tools_total_strict": agents_total + security_total_strict,
        "k8s_count": producers["k8s-audit"]["count"],
        "k8s_count_strict": producers["k8s-audit"]["count_strict"],
        "cyclonedx_count": producers["cyclonedx"]["count"],
        "spdx_count": producers["spdx"]["count"],
        "ocsf_count": producers["ocsf"]["count"],
        "sigstore_count": producers["sigstore-intoto"]["count"],
        "cloud_cloudtrail": cloud_by_id["cloudtrail"],
        "cloud_gcp": cloud_by_id["gcp-audit-logs"],
        "cloud_azure": cloud_by_id["azure-activity-log"],
        "cloud_total": sum(cloud_by_id.values()),
        "mcp_registry_latest": mcp["registry_latest"],
        "mcp_active_excluding_bulk": mcp["active_excluding_bulk"],
    }


def check_floors(numbers: dict[str, int]) -> list[str]:
    problems = []
    if numbers["tools_total"] < TOOLS_FLOOR:
        problems.append(
            f"reach.table.floor_violated: tools_total {numbers['tools_total']} < floor {TOOLS_FLOOR}"
        )
    if numbers["cloud_total"] < CLOUD_FLOOR:
        problems.append(
            f"reach.table.floor_violated: cloud_total {numbers['cloud_total']} < floor {CLOUD_FLOOR}"
        )
    if numbers["mcp_active_excluding_bulk"] < MCP_FLOOR:
        problems.append(
            "reach.table.floor_violated: mcp_active_excluding_bulk "
            f"{numbers['mcp_active_excluding_bulk']} < floor {MCP_FLOOR}"
        )
    return problems


@dataclass(frozen=True)
class ReachRow:
    label: str
    count_key: str
    checked: Callable[[dict[str, Any]], str]
    sources: Callable[[dict[str, Any]], list[str]] = field(default=lambda m: [])
    source_required: bool = True
    aux_count_key: str | None = None


ROWS: list[ReachRow] = [
    ReachRow(
        "OpenTelemetry GenAI and OpenInference traces",
        "agents_total",
        lambda m: m["producers"]["otel-genai"]["checked"],
        lambda m: m["producers"]["otel-genai"]["sources"][:1],
    ),
    ReachRow(
        "Kubernetes audit events",
        "k8s_count",
        lambda m: m["producers"]["k8s-audit"]["checked"],
        lambda m: m["producers"]["k8s-audit"]["sources"][:1],
    ),
    ReachRow(
        "CycloneDX",
        "cyclonedx_count",
        lambda m: m["producers"]["cyclonedx"]["checked"],
        lambda m: m["producers"]["cyclonedx"]["sources"][:1],
    ),
    ReachRow(
        "SPDX",
        "spdx_count",
        lambda m: m["producers"]["spdx"]["checked"],
        lambda m: m["producers"]["spdx"]["sources"][:1],
    ),
    ReachRow(
        "OCSF",
        "ocsf_count",
        lambda m: m["producers"]["ocsf"]["checked"],
        lambda m: m["producers"]["ocsf"]["sources"][:1],
    ),
    ReachRow(
        "Sigstore and in-toto",
        "sigstore_count",
        lambda m: m["producers"]["sigstore-intoto"]["checked"],
        lambda m: m["producers"]["sigstore-intoto"]["sources"][:1],
    ),
    ReachRow(
        "Distinct products, deduplicated",
        "tools_total",
        lambda m: m["producers_meta"]["security"]["checked"],
        source_required=False,
        aux_count_key="tools_total_strict",
    ),
    ReachRow(
        "Cloud audit logs",
        "cloud_total",
        lambda m: m["producers_meta"]["security"]["checked"],
        lambda m: [
            c["source"] for c in m["producers_meta"]["security"]["cloud_services"]
        ],
    ),
    ReachRow(
        "MCP tool declarations",
        "mcp_active_excluding_bulk",
        lambda m: m["producers_meta"]["agents"]["mcp"]["retrieved"][:10],
        lambda m: [m["producers_meta"]["agents"]["mcp"]["source"]],
    ),
]


def _ints(text: str) -> list[int]:
    return [int(m.replace(",", "")) for m in re.findall(r"\d[\d,]*", text)]


def check_reach_section(
    text: str, matrix: dict[str, Any], numbers: dict[str, int], surface: str
) -> list[str]:
    """Scan a committed Reach table: each row's count, check date and source link vs. the matrix."""
    headings = re.findall(re.escape(HEADING), text)
    if not headings:
        return [f"reach.table.missing_section: {surface} has no {HEADING!r} section"]
    if len(headings) > 1:
        return [
            f"reach.table.duplicate_section: {surface} has {len(headings)} {HEADING!r} sections"
        ]
    section = re.search(
        rf"^{re.escape(HEADING)}.*?(?=\n## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert (
        section is not None
    )  # the single heading found above is matched by this same pattern
    body = section.group(0)
    problems = []
    if HEADLINE not in body:
        problems.append(
            f"reach.table.missing_headline: {surface} is missing the locked reach headline"
        )
    for row in ROWS:
        row_pattern = re.compile(
            rf"\|\s*\*{{0,2}}{re.escape(row.label)}\*{{0,2}}\s*\|([^\n|]*)\|([^\n|]*)\|([^\n|]*)\|"
        )
        matches = list(row_pattern.finditer(body))
        if not matches:
            problems.append(
                f"reach.table.missing_section: {surface} has no row for {row.label!r}"
            )
            continue
        if len(matches) > 1:
            problems.append(
                f"reach.table.duplicate_section: {surface} has {len(matches)} rows for "
                f"{row.label!r}"
            )
            continue
        count_cell, checked_cell, source_cell = matches[0].groups()
        found = _ints(count_cell)
        expected = numbers[row.count_key]
        if not found or found[0] != expected:
            problems.append(
                f"reach.table.count_mismatch: {surface} row {row.label!r} states "
                f"{found[0] if found else None}, matrix computes {expected}"
            )
        if row.aux_count_key:
            expected_aux = numbers[row.aux_count_key]
            if len(found) < 2 or found[1] != expected_aux:
                problems.append(
                    f"reach.table.count_mismatch: {surface} row {row.label!r} secondary count "
                    f"states {found[1] if len(found) > 1 else None}, matrix computes {expected_aux}"
                )
        expected_date = row.checked(matrix)
        if expected_date not in checked_cell:
            problems.append(
                f"reach.table.missing_provenance: {surface} row {row.label!r} is missing its "
                f"check date {expected_date!r}"
            )
        if row.source_required:
            for url in row.sources(matrix):
                if url not in source_cell:
                    problems.append(
                        f"reach.table.missing_provenance: {surface} row {row.label!r} is missing "
                        f"its source link {url!r}"
                    )
    return problems


def check_landing_page(text: str) -> list[str]:
    if HEADLINE not in text:
        return [
            "reach.table.missing_section: index.astro does not carry the locked reach headline"
        ]
    return []


def run_check(
    matrix_path: Path = MATRIX_PATH,
    readme_path: Path = README_PATH,
    docs_page_path: Path = DOCS_PAGE_PATH,
    site_page_path: Path = SITE_PAGE_PATH,
    landing_page_path: Path = LANDING_PAGE_PATH,
) -> Counter:
    matrix = load_matrix(matrix_path)
    numbers = reach_numbers(matrix)
    problems = check_floors(numbers)
    problems += check_reach_section(
        readme_path.read_text("utf-8"), matrix, numbers, "README.md"
    )
    problems += check_reach_section(
        docs_page_path.read_text("utf-8"),
        matrix,
        numbers,
        "docs/reference/ingest-support-matrix.md",
    )
    problems += check_reach_section(
        site_page_path.read_text("utf-8"),
        matrix,
        numbers,
        "website/.../reference/ingest-support-matrix.md",
    )
    problems += check_landing_page(landing_page_path.read_text("utf-8"))
    keys = Counter(p.split(":", 1)[0] for p in problems)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
    return keys


def render_good_section(matrix: dict[str, Any], numbers: dict[str, int]) -> str:
    lines = [
        HEADING,
        "",
        HEADLINE,
        "",
        "| Standard | Products confirmed to write it | Checked | Source |",
        "|---|---|---|---|",
    ]
    for row in ROWS:
        checked = row.checked(matrix)
        sources = row.sources(matrix) if row.source_required else []
        source_cell = ", ".join(sources) if sources else "—"
        count_cell = f"{numbers[row.count_key]:,}"
        if row.aux_count_key:
            count_cell += f" ({numbers[row.aux_count_key]:,} counting only self-managed Kubernetes)"
        lines.append(f"| {row.label} | {count_cell} | {checked} | {source_cell} |")
    return "\n".join(lines) + "\n"


def _mutate_first_int(text: str, label: str, new_value: str) -> str:
    """Replace a row's primary count cell value, whatever else that cell contains."""
    pattern = re.compile(rf"(\|\s*{re.escape(label)}\s*\|\s*)\d[\d,]*")
    mutated, n = pattern.subn(lambda m: m.group(1) + new_value, text, count=1)
    assert n == 1, (label, "row not found to mutate")
    return mutated


def self_test() -> None:
    import copy
    import tempfile

    matrix = load_matrix(MATRIX_PATH)
    numbers = reach_numbers(matrix)
    good_table = render_good_section(matrix, numbers)
    dedup_label = "Distinct products, deduplicated"
    cases: list[str] = []

    def run_with(
        readme_text: str,
        docs_text: str = good_table,
        site_text: str = good_table,
        landing_text: str = HEADLINE,
        matrix_path: Path = MATRIX_PATH,
    ) -> Counter:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "README.md").write_text(readme_text, encoding="utf-8")
            (p / "docs.md").write_text(docs_text, encoding="utf-8")
            (p / "site.md").write_text(site_text, encoding="utf-8")
            (p / "index.astro").write_text(landing_text, encoding="utf-8")
            return run_check(
                matrix_path=matrix_path,
                readme_path=p / "README.md",
                docs_page_path=p / "docs.md",
                site_page_path=p / "site.md",
                landing_page_path=p / "index.astro",
            )

    good = run_with(good_table)
    assert good == Counter(), ("a fully consistent set reports nothing", good)
    cases.append("a fully consistent set reports nothing")

    bad_count = _mutate_first_int(good_table, dedup_label, "500")
    got = run_with(bad_count)
    assert got == Counter({"reach.table.count_mismatch": 1}), (
        "a hand-edited count fails",
        got,
    )
    cases.append("a hand-edited count fails with exactly count_mismatch")

    missing = "# No reach section here\n"
    got = run_with(missing)
    assert got == Counter({"reach.table.missing_section": 1}), (
        "a missing section fails",
        got,
    )
    cases.append("a missing section fails with exactly missing_section")

    got = run_with(good_table, landing_text="nothing to see here")
    assert got == Counter({"reach.table.missing_section": 1}), (
        "landing page missing headline",
        got,
    )
    cases.append(
        "a landing page missing the headline fails with exactly missing_section"
    )

    no_source = good_table.replace(
        ROWS[0].sources(matrix)[0], "https://example.invalid/wrong"
    )
    got = run_with(no_source)
    assert got == Counter({"reach.table.missing_provenance": 1}), (
        "a wrong source link fails",
        got,
    )
    cases.append(
        "a row with the wrong source link fails with exactly missing_provenance"
    )

    low_matrix = copy.deepcopy(matrix)
    low_matrix["producers_meta"]["security"]["dedup"]["total"] = 300
    low_matrix["producers_meta"]["security"]["dedup"]["total_strict"] = 240
    low_numbers = reach_numbers(low_matrix)
    low_table = render_good_section(low_matrix, low_numbers)
    with tempfile.TemporaryDirectory() as d:
        low_matrix_path = Path(d) / "matrix.yaml"
        low_matrix_path.write_text(yaml.safe_dump(low_matrix), encoding="utf-8")
        got = run_with(
            low_table,
            docs_text=low_table,
            site_text=low_table,
            matrix_path=low_matrix_path,
        )
    assert got == Counter({"reach.table.floor_violated": 1}), (
        "a real count under the floor fails",
        got,
    )
    cases.append(
        "a real count that has fallen under the 400-tool floor fails with exactly floor_violated"
    )

    bad_aux = re.sub(
        rf"(\|\s*{re.escape(dedup_label)}\s*\|[^|]*?)\d[\d,]*(?=\s*counting only self-managed)",
        lambda m: m.group(1) + "999",
        good_table,
    )
    assert bad_aux != good_table, (
        "the dedup row's secondary (348) count was not found to mutate"
    )
    got = run_with(bad_aux)
    assert got == Counter({"reach.table.count_mismatch": 1}), (
        "a wrong secondary count fails",
        got,
    )
    cases.append(
        "a hand-edited secondary count (the self-managed-only parenthetical) fails with exactly "
        "count_mismatch"
    )

    no_headline = good_table.replace(
        HEADLINE, "A different, unapproved headline sentence."
    )
    got = run_with(no_headline)
    assert got == Counter({"reach.table.missing_headline": 1}), (
        "a missing headline fails",
        got,
    )
    cases.append(
        "a section missing the locked headline fails with exactly missing_headline"
    )

    duplicate_heading = good_table + "\n" + good_table
    got = run_with(duplicate_heading)
    assert got == Counter({"reach.table.duplicate_section": 1}), (
        "a duplicate section fails",
        got,
    )
    cases.append(
        "a surface with the Reach section twice fails with exactly duplicate_section"
    )

    lines = good_table.splitlines()
    dedup_line = next(line for line in lines if line.startswith(f"| {dedup_label} |"))
    duplicate_row = good_table + dedup_line.replace("408", "999") + "\n"
    got = run_with(duplicate_row)
    assert got == Counter({"reach.table.duplicate_section": 1}), (
        "a duplicate row fails",
        got,
    )
    cases.append("a surface with one row repeated fails with exactly duplicate_section")

    print(f"OK self-test {len(cases)}/{len(cases)}: " + "; ".join(cases))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    keys = run_check()
    status = "pass" if not keys else "fail"
    if args.json:
        print(
            json.dumps({"id": "VG-REACH-TABLE", "status": status, "keys": dict(keys)})
        )
    if keys:
        print("FAIL", dict(keys), file=sys.stderr)
        return 1
    print(
        "OK reach counts, check dates and source links agree across README.md, the docs and "
        "website Supported sources pages, and the landing page carries the reach headline; all "
        "three headline floors hold"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
