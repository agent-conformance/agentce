"""Tests for the rule-uniqueness lint (SPEC §7.3, item 18.37): no two rung-2 controls in a catalog
may share an identical shape and fixture set, so a control's rule always tests what its own title
says. Found live in 18.37: DOC-01 shipped with REC-01's shape and fixtures verbatim (baseline and
eu-ai-act), ROB-02 shipped with DAT-01's shape and fixtures verbatim (eu-ai-act), and a further 24
eu-ai-act pairs nobody had named before, disclosed as pre-existing debt in
``verification/gates/fixtures/rule_uniqueness/baseline.json`` (paid down by 18.37a, 18.37b, 18.37c
and the clusters 18.37c/e/f/g split into).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from agentce.catalog import (
    Catalog,
    ControlSpec,
    load_catalog,
    new_rule_uniqueness_problems,
    rule_uniqueness_problems,
    rule_uniqueness_report,
    stale_rule_uniqueness_pairs,
)
from agentce.psp import Predicate, PropertyShape, Shape

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CATALOGS_ROOT = _REPO_ROOT / "spec" / "catalogs"
_BASE_CATALOGS = _CATALOGS_ROOT / "base"
_OVERLAY_CATALOGS = _CATALOGS_ROOT / "overlays"
_BASELINE_FILE = (
    _REPO_ROOT
    / "verification"
    / "gates"
    / "fixtures"
    / "rule_uniqueness"
    / "baseline.json"
)


def _baseline_pairs(catalog_name: str) -> frozenset[frozenset[str]]:
    data = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    return frozenset(frozenset(pair) for pair in data["pairs"][catalog_name])


def _control(cid: str, test_cases: list[dict[str, str]] | None = None) -> ControlSpec:
    return ControlSpec(
        id=cid,
        version="2026.09",
        title="t",
        applies_to_roles=["both"],
        mode="automated",
        rung=2,
        severity="high",
        min_source_class="self_report",
        minimum_evidence=[],
        shape_path=f"shapes/{cid}.ttl",
        tolerance={"kind": "count", "max": 0},
        test_cases=test_cases or [],
    )


def _shape(iri: str, path: str = "prov:wasAssociatedWith") -> Shape:
    return Shape(
        iri=iri,
        target_class="agentce:ConsequentialDecision",
        properties=[PropertyShape(path=Predicate(path), name="S1", min_count=1)],
    )


def _catalog(
    directory: Path, controls: list[ControlSpec], shapes: dict[str, Shape]
) -> Catalog:
    return Catalog(
        id="t", version="2026.09", directory=directory, controls=controls, shapes=shapes
    )


def test_distinct_shapes_are_not_a_problem(tmp_path: Path) -> None:
    controls = [_control("REC-01"), _control("DOC-01")]
    shapes = {
        "agentce:REC-01-Shape": _shape(
            "agentce:REC-01-Shape", "prov:wasAssociatedWith"
        ),
        "agentce:DOC-01-Shape": _shape(
            "agentce:DOC-01-Shape", "agentce:componentDeclared"
        ),
    }
    assert rule_uniqueness_problems(_catalog(tmp_path, controls, shapes)) == []


def test_identical_shape_and_fixtures_is_a_problem(tmp_path: Path) -> None:
    fixture = tmp_path / "test" / "shared.jsonl"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"id":"d1"}\n', encoding="utf-8")
    case = [{"id": "pass", "expected": "passed", "fixture": "test/shared.jsonl"}]
    controls = [_control("REC-01", case), _control("DOC-01", case)]
    same_shape = _shape("agentce:REC-01-Shape", "prov:wasAssociatedWith")
    shapes = {
        "agentce:REC-01-Shape": same_shape,
        # DOC-01 ships REC-01's shape verbatim (the 18.37 regression): the control-id suffix differs
        # by IRI only, the shape content is byte-for-byte the same object.
        "agentce:DOC-01-Shape": Shape(
            iri="agentce:DOC-01-Shape",
            target_class=same_shape.target_class,
            properties=same_shape.properties,
        ),
    }
    problems = rule_uniqueness_problems(_catalog(tmp_path, controls, shapes))
    assert len(problems) == 1
    assert "DOC-01" in problems[0] and "REC-01" in problems[0]


def test_same_shape_but_different_fixtures_is_not_a_problem(tmp_path: Path) -> None:
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "a.jsonl").write_text('{"id":"a"}\n', encoding="utf-8")
    (tmp_path / "test" / "b.jsonl").write_text('{"id":"b"}\n', encoding="utf-8")
    controls = [
        _control(
            "REC-01", [{"id": "pass", "expected": "passed", "fixture": "test/a.jsonl"}]
        ),
        _control(
            "DOC-01", [{"id": "pass", "expected": "passed", "fixture": "test/b.jsonl"}]
        ),
    ]
    same_shape = _shape("agentce:REC-01-Shape")
    shapes = {
        "agentce:REC-01-Shape": same_shape,
        "agentce:DOC-01-Shape": Shape(
            iri="agentce:DOC-01-Shape",
            target_class=same_shape.target_class,
            properties=same_shape.properties,
        ),
    }
    assert rule_uniqueness_problems(_catalog(tmp_path, controls, shapes)) == []


_ALL_CATALOGS = {
    "baseline": _BASE_CATALOGS / "baseline",
    "eu-ai-act": _BASE_CATALOGS / "eu-ai-act",
    "nist-ai-rmf": _BASE_CATALOGS / "nist-ai-rmf",
    "conduct": _OVERLAY_CATALOGS / "conduct",
    "employment": _OVERLAY_CATALOGS / "employment",
    "finance": _OVERLAY_CATALOGS / "finance",
    "insurance": _OVERLAY_CATALOGS / "insurance",
}


@pytest.mark.parametrize("name", sorted(_ALL_CATALOGS))
def test_shipped_catalog_has_no_duplicate_rules_beyond_the_committed_baseline(
    name: str,
) -> None:
    """The gate's real invariant, over every shipped base and overlay catalog (not just the three
    named in the baseline file -- the check driver discovers catalogs from the directory tree, so
    this test covers the same ground): no NEW duplicate beyond the disclosed, committed baseline. A
    duplicate pair already named in baseline.json is pre-existing debt (owned by 18.37a/18.37b/
    18.37c), not a gate failure; see the module docstring and the baseline file's own description."""
    catalog = load_catalog(_ALL_CATALOGS[name])
    assert new_rule_uniqueness_problems(catalog, _baseline_pairs(name)) == []


@pytest.mark.parametrize("name", sorted(_ALL_CATALOGS))
def test_committed_baseline_pairs_are_still_real(name: str) -> None:
    """The baseline only ever shrinks: every pair committed in baseline.json must still be a real
    duplicate in the shipped catalog, so a stale entry (one already fixed) is caught and removed
    rather than silently continuing to shadow a check that no longer needs it."""
    catalog = load_catalog(_ALL_CATALOGS[name])
    assert stale_rule_uniqueness_pairs(catalog, _baseline_pairs(name)) == frozenset()


def test_a_stale_baseline_pair_that_no_longer_duplicates_is_caught(
    tmp_path: Path,
) -> None:
    """The negative case `test_committed_baseline_pairs_are_still_real` guards against: a baseline
    names a pair that the catalog, as shipped, no longer actually duplicates (e.g. the fix landed but
    the baseline entry was never removed). `stale_rule_uniqueness_pairs` must flag it rather than
    silently continuing to treat it as disclosed debt."""
    controls = [_control("REC-01"), _control("DOC-01")]
    shapes = {
        "agentce:REC-01-Shape": _shape(
            "agentce:REC-01-Shape", "prov:wasAssociatedWith"
        ),
        # DOC-01 now has its own distinct shape -- the duplicate was fixed -- but the baseline file
        # below still names the old pair.
        "agentce:DOC-01-Shape": _shape(
            "agentce:DOC-01-Shape", "agentce:componentDeclared"
        ),
    }
    catalog = _catalog(tmp_path, controls, shapes)
    stale_baseline = frozenset({frozenset({"DOC-01", "REC-01"})})
    assert stale_rule_uniqueness_pairs(catalog, stale_baseline) == stale_baseline
    # A stale entry is not "new" (it was already disclosed), but the file is wrong to keep it.
    assert new_rule_uniqueness_problems(catalog, stale_baseline) == []


def test_an_undisclosed_catalog_with_zero_duplicates_has_no_problems(
    tmp_path: Path,
) -> None:
    """A catalog absent from the baseline file entirely (e.g. a brand-new overlay the baseline
    author never named) must still be checkable: `new_rule_uniqueness_problems` with an empty
    baseline reports every real duplicate, and reports none when there aren't any -- the check
    driver's directory-tree discovery relies on this default-to-empty behaviour."""
    controls = [_control("REC-01"), _control("DOC-01")]
    shapes = {
        "agentce:REC-01-Shape": _shape(
            "agentce:REC-01-Shape", "prov:wasAssociatedWith"
        ),
        "agentce:DOC-01-Shape": _shape(
            "agentce:DOC-01-Shape", "agentce:componentDeclared"
        ),
    }
    catalog = _catalog(tmp_path, controls, shapes)
    assert new_rule_uniqueness_problems(catalog, frozenset()) == []
    assert stale_rule_uniqueness_pairs(catalog, frozenset()) == frozenset()


def _load_check_driver():
    """The driver (`verification/gates/catalog_rule_uniqueness_check.py`) isn't on the normal
    import path -- load it the same way its own shell wrapper invokes it, so the shrink-only
    ceiling logic inside `main()` is tested directly rather than only through the shell gate."""
    spec = importlib.util.spec_from_file_location(
        "catalog_rule_uniqueness_check",
        _REPO_ROOT / "verification" / "gates" / "catalog_rule_uniqueness_check.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_baseline_above_its_pinned_ceiling_is_caught(tmp_path: Path) -> None:
    """The shrink-only check `baseline.json`'s own `ceilings` entry exists for: without it, a
    change could declare a brand-new duplicate "already disclosed" by adding it to `pairs` in the
    same change, and `new_rule_uniqueness_problems` alone would not catch it (the pair IS in the
    baseline it's compared against). The ceiling caps the disclosed-pair count itself, so a baseline
    that grew past its pinned floor fails even though every pair it lists is a real duplicate."""
    catalogs_root = tmp_path / "catalogs"
    (catalogs_root / "base").mkdir(parents=True)
    shutil.copytree(_BASE_CATALOGS / "baseline", catalogs_root / "base" / "baseline")
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {"ceilings": {"baseline": 0}, "pairs": {"baseline": [["DOC-01", "REC-01"]]}}
        ),
        encoding="utf-8",
    )
    status = _load_check_driver().main(["prog", str(catalogs_root), str(baseline_path)])
    assert status == 1


def test_a_baseline_at_its_pinned_ceiling_is_not_caught(tmp_path: Path) -> None:
    """The positive case: a baseline whose disclosed-pair count sits exactly at its pinned ceiling
    (today's real, committed state) must not fail on the ceiling check alone."""
    catalogs_root = tmp_path / "catalogs"
    (catalogs_root / "base").mkdir(parents=True)
    shutil.copytree(_BASE_CATALOGS / "baseline", catalogs_root / "base" / "baseline")
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {"ceilings": {"baseline": 1}, "pairs": {"baseline": [["DOC-01", "REC-01"]]}}
        ),
        encoding="utf-8",
    )
    status = _load_check_driver().main(["prog", str(catalogs_root), str(baseline_path)])
    assert status == 0


@pytest.mark.parametrize("name", sorted(_ALL_CATALOGS))
def test_rule_uniqueness_report_matches_the_two_separate_calls(name: str) -> None:
    """`rule_uniqueness_report` exists so the check driver computes `_rule_uniqueness_pairs` (which
    re-parses every rung-2 control's shape and fixtures) once per catalog instead of twice; it must
    give the exact same answer as calling `stale_rule_uniqueness_pairs` and
    `new_rule_uniqueness_problems` separately."""
    catalog = load_catalog(_ALL_CATALOGS[name])
    baseline = _baseline_pairs(name)
    stale, problems = rule_uniqueness_report(catalog, baseline)
    assert stale == stale_rule_uniqueness_pairs(catalog, baseline)
    assert problems == new_rule_uniqueness_problems(catalog, baseline)


def test_seeded_fault_copying_rec_01s_shape_into_doc_01_is_caught(
    tmp_path: Path,
) -> None:
    """The exact 18.37 seeded fault: a hostile/careless catalog edit copies one control's shape and
    fixtures onto another's. A real catalog copy, mutated the way the regression actually shipped."""
    copy = tmp_path / "baseline"
    shutil.copytree(_BASE_CATALOGS / "baseline", copy)
    (copy / "shapes" / "DOC-01.ttl").write_bytes(
        (copy / "shapes" / "REC-01.ttl").read_bytes()
    )
    for case_id in ("passed", "failed", "inapplicable"):
        (copy / "test" / "DOC-01" / f"{case_id}.jsonl").write_bytes(
            (copy / "test" / "REC-01" / f"{case_id}.jsonl").read_bytes()
        )
    catalog = load_catalog(copy)
    problems = rule_uniqueness_problems(catalog)
    assert any("DOC-01" in p and "REC-01" in p for p in problems), problems
