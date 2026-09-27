"""The baseline catalog is a profile of existing standards, and it is the default lens.

Every baseline control cites at least two standards; every citation is one the standards crosswalk
files or the standard-specific catalogs already cite for that control, so the baseline adds no
requirement of its own; an assessment that names no catalog evaluates it; every report lists the lenses a run can choose; and the catalog is
byte-identical in the specification tree and in each engine's bundle.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentce import bundled, cli
from agentce.assertions import aggregate
from agentce.catalog import lint_catalog
from agentce.report import render_report_html, render_report_md

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base"
_BASELINE = _BASE / "baseline"
_CROSSWALKS = _BASE / "eu-ai-act" / "crosswalk"
#: The standard-specific catalogs whose controls the baseline's detection is taken from.
_STANDARD_CATALOGS = ("eu-ai-act", "nist-ai-rmf")
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
#: The engines' vendored copies of the baseline (Python's is the one ``bundled`` resolves).
_BUNDLES = {
    "python": bundled.catalogs_dir() / "base" / "baseline",
    "typescript": _REPO_ROOT
    / "engines"
    / "typescript"
    / "data"
    / "catalogs"
    / "base"
    / "baseline",
    "java": _REPO_ROOT
    / "engines"
    / "java"
    / "src"
    / "main"
    / "resources"
    / "catalogs"
    / "base"
    / "baseline",
}
#: Standards whose crosswalk maps requirement areas, not clauses: never cited by the baseline.
_AREA_LEVEL = frozenset({"aiuc-1"})
#: A control's ``crosswalk`` names a standard by its schema id; the crosswalk files by their own.
_CROSSWALK_FRAMEWORK = {"iso-42001": "iso-iec-42001"}


def _mapped(control: str) -> set[tuple[str, str]]:
    """Every (standard, clause) already cited for ``control``: by the active standards crosswalk files
    (never a placeholder or a requirement-area mapping) or by a standard-specific catalog."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_CROSSWALKS.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("status") == "placeholder" or data["framework"] in _AREA_LEVEL:
            continue
        for obligation in data["obligations"]:
            if control in (obligation.get("controls") or []):
                found.add((data["framework"], obligation["ref"]))
    for catalog in _STANDARD_CATALOGS:
        path = _BASE / catalog / "controls" / f"{control}.yaml"
        if path.is_file():
            for entry in yaml.safe_load(path.read_text(encoding="utf-8"))["crosswalk"]:
                found.add((entry["framework"], entry["clause"]))
    return found


def _invented_citations(catalog: Path) -> list[str]:
    """Baseline citations that neither a crosswalk file nor a standard-specific catalog gives that control."""
    invented: list[str] = []
    for path in sorted((catalog / "controls").glob("*.yaml")):
        control = yaml.safe_load(path.read_text(encoding="utf-8"))
        mapped = _mapped(control["id"])
        for entry in control["crosswalk"]:
            framework = _CROSSWALK_FRAMEWORK.get(entry["framework"], entry["framework"])
            if (framework, entry["clause"]) not in mapped:
                invented.append(
                    f"{control['id']}: {entry['framework']} {entry['clause']}"
                )
    return invented


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != ".DS_Store" and "__pycache__" not in p.parts
    }


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "baseline"
    shutil.copytree(_BASELINE, target)
    return target


def test_the_baseline_lints_clean_with_provenance() -> None:
    assert lint_catalog(_BASELINE, require_provenance=True) == []


def test_every_baseline_control_meets_the_two_standard_floor() -> None:
    meta = yaml.safe_load((_BASELINE / "catalog.yaml").read_text(encoding="utf-8"))
    assert meta["min_crosswalk_frameworks"] == 2
    for path in sorted((_BASELINE / "controls").glob("*.yaml")):
        control = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert len({e["framework"] for e in control["crosswalk"]}) >= 2, control["id"]


def _rewrite_control(
    catalog: Path, control: str, edit: Callable[[dict[str, Any]], None]
) -> None:
    path = catalog / "controls" / f"{control}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _rewrite_meta(catalog: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    path = catalog / "catalog.yaml"
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(meta)
    path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


def test_a_control_citing_one_standard_fails_the_floor(tmp_path: Path) -> None:
    catalog = _copy(tmp_path)
    _rewrite_control(
        catalog, "REC-01", lambda d: d.update(crosswalk=d["crosswalk"][:1])
    )
    problems = lint_catalog(catalog)
    assert any(
        p.startswith("catalog.crosswalk_floor: REC-01 cites 1 standard(s)")
        for p in problems
    )


def test_the_same_standard_cited_twice_counts_once_for_the_floor(
    tmp_path: Path,
) -> None:
    catalog = _copy(tmp_path)

    def one_standard_twice(d: dict[str, Any]) -> None:
        first = d["crosswalk"][0]
        d["crosswalk"] = [first, {**first, "clause": first["clause"] + " bis"}]

    _rewrite_control(catalog, "REC-01", one_standard_twice)
    assert any(
        p.startswith("catalog.crosswalk_floor: REC-01 cites 1")
        for p in lint_catalog(catalog)
    )


def test_an_unknown_or_case_variant_standard_is_not_a_second_standard(
    tmp_path: Path,
) -> None:
    catalog = _copy(tmp_path)

    def variant(d: dict[str, Any]) -> None:
        first = d["crosswalk"][0]
        d["crosswalk"] = [first, {**first, "framework": first["framework"].upper()}]

    _rewrite_control(catalog, "REC-01", variant)
    assert any("REC-01.yaml: schema:" in p for p in lint_catalog(catalog))


def test_the_floor_only_applies_to_a_catalog_that_sets_it(tmp_path: Path) -> None:
    catalog = _copy(tmp_path)
    _rewrite_meta(catalog, lambda m: m.pop("min_crosswalk_frameworks"))
    _rewrite_control(
        catalog, "REC-01", lambda d: d.update(crosswalk=d["crosswalk"][:1])
    )
    assert lint_catalog(catalog) == []


@pytest.mark.parametrize("floor", ["2", True, -1, 2.5])
def test_a_malformed_floor_is_a_lint_problem(tmp_path: Path, floor: object) -> None:
    catalog = _copy(tmp_path)
    _rewrite_meta(catalog, lambda m: m.update(min_crosswalk_frameworks=floor))
    assert any("min_crosswalk_frameworks must be" in p for p in lint_catalog(catalog))


def test_no_baseline_citation_is_invented() -> None:
    assert _invented_citations(_BASELINE) == []


def test_the_baseline_covers_the_shared_areas_and_cites_only_clause_level_standards() -> (
    None
):
    families = {
        yaml.safe_load(p.read_text(encoding="utf-8"))["id"].split("-")[0]
        for p in (_BASELINE / "controls").glob("*.yaml")
    }
    # record-keeping, human oversight, transparency, robustness, incidents, documentation
    assert {"REC", "OVS", "TRN", "ROB", "INC", "DOC"} <= families
    for path in (_BASELINE / "controls").glob("*.yaml"):
        control = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert not {e["framework"] for e in control["crosswalk"]} & _AREA_LEVEL, (
            control["id"]
        )


@pytest.mark.parametrize(
    ("added", "expected"),
    [
        (
            {"framework": "nist-ai-rmf", "clause": "GOVERN 9.9", "relation": "maps"},
            "REC-01: nist-ai-rmf GOVERN 9.9",
        ),
        (
            {"framework": "nist-ai-rmf", "clause": "", "relation": "maps"},
            "REC-01: nist-ai-rmf ",
        ),
        (
            # a real crosswalk row, but requirement-area granularity: not a clause citation
            {"framework": "aiuc-1", "clause": "Accountability", "relation": "maps"},
            "REC-01: aiuc-1 Accountability",
        ),
        (
            # a row that exists only in a placeholder (draft) crosswalk
            {"framework": "iso-iec-24970", "clause": "log-content", "relation": "maps"},
            "REC-01: iso-iec-24970 log-content",
        ),
    ],
)
def test_an_invented_citation_is_caught(
    tmp_path: Path, added: dict[str, str], expected: str
) -> None:
    catalog = _copy(tmp_path)
    _rewrite_control(catalog, "REC-01", lambda d: d["crosswalk"].append(added))
    assert _invented_citations(catalog) == [expected]


def test_each_baseline_control_differs_from_its_source_only_in_its_citations() -> None:
    """The baseline adds no rule of its own: same control, shape and fixtures as the EU AI Act catalog."""
    source = _BASE / "eu-ai-act"
    for path in sorted((_BASELINE / "controls").glob("*.yaml")):
        ours = yaml.safe_load(path.read_text(encoding="utf-8"))
        theirs = yaml.safe_load(
            (source / "controls" / path.name).read_text(encoding="utf-8")
        )
        ours.pop("crosswalk")
        theirs.pop("crosswalk")
        assert ours == theirs, path.name
        control = path.stem
        assert (_BASELINE / "shapes" / f"{control}.ttl").read_bytes() == (
            source / "shapes" / f"{control}.ttl"
        ).read_bytes()
        assert _files(_BASELINE / "test" / control) == _files(source / "test" / control)
    assert (_BASELINE / "test" / "domain.yaml").read_bytes() == (
        source / "test" / "domain.yaml"
    ).read_bytes()


def test_the_default_lens_is_the_baseline_and_one_of_the_lenses() -> None:
    assert bundled.DEFAULT_LENS == "baseline@2026.09"
    assert bundled.DEFAULT_LENS in bundled.base_lenses()
    assert {"eu-ai-act@2026.09", "nist-ai-rmf@2026.09"} <= set(bundled.base_lenses())


def _assess_without_a_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    profile = yaml.safe_load(
        (_QUICKSTART / "applicability.yaml").read_text(encoding="utf-8")
    )
    del profile["catalogs"]
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(tmp_path / "profile.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            "--out",
            str(out),
        ]
    )
    capsys.readouterr()
    assert code == 0
    return out


def test_an_assessment_naming_no_catalog_evaluates_only_the_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _assess_without_a_catalog(tmp_path, capsys)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [f"{c['id']}@{c['version']}" for c in manifest["inputs"]["catalogs"]] == [
        bundled.DEFAULT_LENS
    ]
    assert "- Catalog: baseline@2026.09\n" in (out / "report.md").read_text(
        encoding="utf-8"
    )


def _lens_list(report: str) -> tuple[list[str], str, str]:
    """The lens section of a report: the lens ids, the one marked default, and the instruction."""
    match = re.search(
        r"^- Lenses available: (.+?); (choose one with .+)$", report, re.MULTILINE
    )
    assert match, "the report has no lens list"
    lenses = [item.strip() for item in match.group(1).split(", ")]
    defaults = [
        item.removesuffix(" (default)")
        for item in lenses
        if item.endswith(" (default)")
    ]
    assert len(defaults) <= 1
    return (
        [item.removesuffix(" (default)") for item in lenses],
        "".join(defaults),
        match.group(2),
    )


def test_every_report_lists_the_lenses_and_marks_the_default() -> None:
    on_disk = sorted(
        f"{m['id']}@{m['version']}"
        for m in (
            yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in _BASE.glob("*/catalog.yaml")
        )
    )
    counts = aggregate([])
    lenses, default, instruction = _lens_list(render_report_md([], counts))
    assert lenses == on_disk
    assert default == bundled.DEFAULT_LENS
    assert instruction == "choose one with --catalog <id@version>"
    html = render_report_html([], counts)
    assert "<li>Lenses available: " in html and "baseline@2026.09 (default)" in html


def test_the_lens_list_follows_the_catalogs_the_engine_ships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "base" / "extra").mkdir(parents=True)
    (tmp_path / "base" / "extra" / "catalog.yaml").write_text(
        'id: extra\nversion: "1"\n', encoding="utf-8"
    )
    monkeypatch.setattr(bundled, "catalogs_dir", lambda: tmp_path)
    lenses, _, _ = _lens_list(render_report_md([], aggregate([])))
    assert lenses == ["extra@1"]


def test_the_default_report_cites_two_standards_for_every_baseline_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _assess_without_a_catalog(tmp_path, capsys) / "report.md"
    blocks = re.split(
        r"^- \*\*", report.read_text(encoding="utf-8"), flags=re.MULTILINE
    )[1:]
    assert blocks
    for block in blocks:
        cited = re.findall(
            r"^  - ([a-z0-9-]+) \S.*\(clause reference unverified\)$",
            block,
            re.MULTILINE,
        )
        assert len(set(cited)) >= 2, block.splitlines()[0]


def test_the_missing_catalog_message_matches_the_catalogue() -> None:
    catalogue = json.loads(
        (_REPO_ROOT / "spec" / "i18n" / "messages.en.json").read_text("utf-8")
    )
    cause = catalogue["errors.input.catalog_missing.cause"]
    fix = catalogue["errors.input.catalog_missing.fix"]
    sources = [
        _REPO_ROOT / "engines" / "python" / "agentce" / "commands" / "__init__.py",
        _REPO_ROOT / "engines" / "typescript" / "src" / "cli.ts",
        _REPO_ROOT
        / "engines"
        / "java"
        / "src"
        / "main"
        / "java"
        / "org"
        / "agentce"
        / "Cli.java",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert cause in text and fix in text, source.name


def test_the_baseline_is_identical_in_the_specification_and_every_engine_bundle() -> (
    None
):
    original = _files(_BASELINE)
    assert "catalog.sig.json" in original
    for engine, root in _BUNDLES.items():
        assert _files(root) == original, engine
