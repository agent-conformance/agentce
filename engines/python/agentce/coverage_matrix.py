"""Automation coverage matrix (SPEC §7.5).

The matrix counts, per control family, how many controls the catalog evaluates automatically, how
many are semi-automated, and how many are manual. It is regenerated from the control files at build
time; a committed copy lives beside the catalog, and ``agentce catalog coverage-matrix <dir> --check``
fails if the two diverge. The matrix is informative — it never changes an outcome.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .catalog import load_catalog

MATRIX_FILE = "automation-coverage.md"
_MODES = ("automated", "semi-automated", "manual")


def _family_order(catalog_dir: Path, families_seen: list[str]) -> list[str]:
    meta = (
        yaml.safe_load((catalog_dir / "catalog.yaml").read_text(encoding="utf-8")) or {}
    )
    declared = [str(f) for f in meta.get("families", [])]
    ordered = [f for f in declared if f in families_seen]
    ordered += [f for f in families_seen if f not in ordered]
    return ordered


def automation_matrix(catalog_dir: Path) -> str:
    """Render the §7.5 automation coverage matrix for the catalog at ``catalog_dir`` as Markdown."""
    catalog = load_catalog(catalog_dir)
    counts: dict[str, dict[str, int]] = {}
    for control in catalog.controls:
        family = control.id.split("-", 1)[0]
        row = counts.setdefault(family, {"total": 0, **{m: 0 for m in _MODES}})
        row["total"] += 1
        if control.mode in row:
            row[control.mode] += 1
    families = _family_order(catalog_dir, sorted(counts))
    lines = [
        f"# Automation coverage matrix — {catalog.id}@{catalog.version} (SPEC §7.5)",
        "",
        "Generated from the control files by `agentce catalog coverage-matrix`; do not edit by hand.",
        "",
        "| Family | Controls | Automated | Semi-automated | Manual |",
        "|---|---|---|---|---|",
    ]
    totals = {"total": 0, **{m: 0 for m in _MODES}}
    for family in families:
        row = counts[family]
        lines.append(
            f"| {family} | {row['total']} | {row['automated']} | "
            f"{row['semi-automated']} | {row['manual']} |"
        )
        for key in totals:
            totals[key] += row[key]
    lines.append(
        f"| **Total** | {totals['total']} | {totals['automated']} | "
        f"{totals['semi-automated']} | {totals['manual']} |"
    )
    return "\n".join(lines) + "\n"


def check_matrix(catalog_dir: Path) -> tuple[bool, str]:
    """Return ``(ok, message)``: whether the committed matrix equals the regenerated one."""
    generated = automation_matrix(catalog_dir)
    committed_path = catalog_dir / MATRIX_FILE
    if not committed_path.is_file():
        return False, f"no committed matrix at {committed_path}"
    committed = committed_path.read_text(encoding="utf-8")
    if committed != generated:
        return False, f"committed matrix at {committed_path} is stale; regenerate it"
    return True, "MATRIX OK"


def write_matrix(catalog_dir: Path) -> Path:
    path = catalog_dir / MATRIX_FILE
    path.write_text(automation_matrix(catalog_dir), encoding="utf-8")
    return path
