"""The base catalog lints clean, and lint catches a broken control or fixture.

Also covers ``agentce catalog init`` (18.9 C1): a from-scratch, lint-clean, ``mode: automated``
scaffold, the guided path's first step.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce import cli, commands
from agentce.catalog import lint_catalog, load_catalog
from agentce.domain import DomainBinding
from agentce.errors import InputError
from agentce.graph import build_graph
from agentce.structural import evaluate_control

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def _mutate_shape(tmp_path: Path, triple: str) -> Path:
    """Copy the base catalog and append ``triple`` to DAT-01's top-level shape (mirrors each other
    engine's own ``mutateShape``/``withMutatedCatalog`` test helper for this item)."""
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    shape = catalog_dir / "shapes" / "DAT-01.ttl"
    shape.write_text(
        shape.read_text(encoding="utf-8").replace(
            'sh:name "S1" ] .', f'sh:name "S1" ] ; {triple} .'
        ),
        encoding="utf-8",
    )
    return catalog_dir


def _run(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _scaffold_files(directory: Path, family: str) -> list[Path]:
    return [
        directory / "catalog.yaml",
        directory / "controls" / f"{family}-01.yaml",
        directory / "shapes" / f"{family}-01.ttl",
        directory / "test" / "domain.yaml",
        directory / "test" / f"{family}-01" / "passed.jsonl",
        directory / "test" / f"{family}-01" / "failed.jsonl",
        directory / "test" / f"{family}-01" / "inapplicable.jsonl",
    ]


def test_base_catalog_lints_clean() -> None:
    assert lint_catalog(_BASE) == []


def test_load_base_catalog() -> None:
    catalog = load_catalog(_BASE)
    ids = {control.id for control in catalog.controls}
    assert {"OVS-03", "REC-04", "INT-01", "INC-02"} <= ids


def test_vendored_control_schema_matches_spec() -> None:
    vendored = (
        _REPO_ROOT / "engines/python/agentce/data/schemas/control.schema.json"
    ).read_text(encoding="utf-8")
    spec = (_REPO_ROOT / "spec/rules/control.schema.json").read_text(encoding="utf-8")
    assert vendored == spec


def test_lint_detects_broken_fixture(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    fixture = catalog_dir / "test" / "OVS-03" / "passed.jsonl"
    # Break the human chain terminus so the "passed" fixture no longer passes.
    fixture.write_text(
        fixture.read_text(encoding="utf-8")
        .replace('"kind": "human"', '"kind": "service"')
        .replace('"kind":"human"', '"kind":"service"'),
        encoding="utf-8",
    )
    problems = lint_catalog(catalog_dir)
    assert any("OVS-03" in problem for problem in problems)


def test_lint_detects_schema_violation(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    control = catalog_dir / "controls" / "INT-01.yaml"
    control.write_text(
        control.read_text(encoding="utf-8").replace(
            "severity: high", "severity: catastrophic"
        ),
        encoding="utf-8",
    )
    problems = lint_catalog(catalog_dir)
    assert any("INT-01" in problem and "schema" in problem for problem in problems)


# --- 18.34: the Portable Shape Profile forbids sh:sparql and sh:js; both lint and load refuse them. ---


@pytest.mark.parametrize(
    ("triple", "key"),
    [
        ("sh:sparql [] ", "catalog.shape.sparql_forbidden"),
        ("sh:js [] ", "catalog.shape.script_forbidden"),
        ("sh:javascript [] ", "catalog.shape.script_forbidden"),
    ],
)
def test_lint_detects_forbidden_shape_predicate(
    tmp_path: Path, triple: str, key: str
) -> None:
    catalog_dir = _mutate_shape(tmp_path, triple)
    problems = lint_catalog(catalog_dir)
    assert any(key in problem for problem in problems)


@pytest.mark.parametrize(
    ("triple", "key"),
    [
        ("sh:sparql [] ", "catalog.shape.sparql_forbidden"),
        ("sh:js [] ", "catalog.shape.script_forbidden"),
        ("sh:javascript [] ", "catalog.shape.script_forbidden"),
    ],
)
def test_load_refuses_forbidden_shape_predicate(
    tmp_path: Path, triple: str, key: str
) -> None:
    catalog_dir = _mutate_shape(tmp_path, triple)
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == key


def test_load_refuses_a_shape_carrying_both_predicates_as_sparql_deterministically(
    tmp_path: Path,
) -> None:
    """Contract-critic round 1 (B1): a shape with BOTH predicates must always report sparql first
    (matching spec/rules/psp_check.py's PRIORITY_DENY), never whichever one the graph happens to
    iterate first -- the first cut iterated `graph.predicates()` directly (a set, ordered by
    PYTHONHASHSEED) and disagreed with itself across runs, and with TypeScript/Java."""
    catalog_dir = _mutate_shape(tmp_path, "sh:js [] ; sh:sparql [] ")
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.shape.sparql_forbidden"


def test_load_refuses_forbidden_predicate_under_an_aliased_prefix_or_bare_iri(
    tmp_path: Path,
) -> None:
    """A check that only matched the literal string `sh:sparql` would miss both of these: the
    predicate is a resolved IRI, not textual prefix syntax, so a shape author using a different
    prefix for the SHACL namespace, or the bare IRI, is still caught."""
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    shape = catalog_dir / "shapes" / "DAT-01.ttl"
    text = shape.read_text(encoding="utf-8")
    aliased = text.replace(
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix shacl: <http://www.w3.org/ns/shacl#> .",
    ).replace('sh:name "S1" ] .', 'sh:name "S1" ] ; shacl:sparql [] .')
    shape.write_text(aliased, encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.shape.sparql_forbidden"

    bare_iri = text.replace(
        'sh:name "S1" ] .', 'sh:name "S1" ] ; <http://www.w3.org/ns/shacl#js> [] .'
    )
    shape.write_text(bare_iri, encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.shape.script_forbidden"


def test_load_does_not_refuse_a_literal_that_merely_mentions_sh_sparql(
    tmp_path: Path,
) -> None:
    """An RDF predicate is always an IRI, never a literal, so a string value that happens to
    contain the text "sh:sparql" (documentation, a control's own prose) can never be mistaken for
    the forbidden predicate -- unlike a plain substring-over-the-file-text check, which would be
    fooled by this."""
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    shape = catalog_dir / "shapes" / "DAT-01.ttl"
    shape.write_text(
        shape.read_text(encoding="utf-8").replace(
            'sh:name "S1" ] .',
            'sh:name "S1, not sh:sparql or sh:js (documentation only)" ] .',
        ),
        encoding="utf-8",
    )
    load_catalog(catalog_dir)  # must not raise


def test_load_does_not_refuse_a_turtle_comment_that_merely_mentions_sh_sparql(
    tmp_path: Path,
) -> None:
    """A `#`-comment is not RDF data at all; a substring-over-the-raw-file-text check would still
    be fooled by one naming "sh:sparql", unlike an RDF-predicate-based check."""
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    shape = catalog_dir / "shapes" / "DAT-01.ttl"
    shape.write_text(
        "# this shape must never use sh:sparql or sh:js\n"
        + shape.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    load_catalog(catalog_dir)  # must not raise


def test_load_refuses_forbidden_predicate_nested_inside_a_property_shape(
    tmp_path: Path,
) -> None:
    """The forbidden predicate can appear on any node in the graph, not only the top-level node
    shape; a check that only inspected the node shape's own triples would miss a property shape
    nesting the SPARQL/script construct inside `sh:property [...]`."""
    catalog_dir = tmp_path / "cat"
    shutil.copytree(_BASE, catalog_dir)
    shape = catalog_dir / "shapes" / "DAT-01.ttl"
    shape.write_text(
        shape.read_text(encoding="utf-8").replace(
            'sh:property [ sh:path prov:used ; sh:minCount 1 ; sh:name "S1" ] .',
            'sh:property [ sh:path prov:used ; sh:minCount 1 ; sh:name "S1" ; sh:sparql [] ] .',
        ),
        encoding="utf-8",
    )
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.shape.sparql_forbidden"


def test_load_refuses_forbidden_predicate_written_as_a_turtle_unicode_escape(
    tmp_path: Path,
) -> None:
    """Turtle's IRIREF grammar allows `\\uXXXX`/`\\UXXXXXXXX` escapes inside `<...>`; a predicate
    scan that compared raw, undecoded IRI text (rather than the resolved IRI) would miss
    `<http://www.w3.org/ns/shacl#sp\\u0061rql>`, which is the same IRI as `sh:sparql` once decoded."""
    catalog_dir = _mutate_shape(
        tmp_path, "<http://www.w3.org/ns/shacl#sp\\u0061rql> [] "
    )
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.shape.sparql_forbidden"


def test_load_and_lint_accept_the_unmutated_base_catalog_clean() -> None:
    """Explicit clean-base assertion for the gate's own selection (round-2 critic B3'), on top of
    the already-passing `structuralEvaluationMatchesReference`-style coverage elsewhere: the real
    `eu-ai-act` catalog's real shapes must never themselves trip the new check."""
    load_catalog(_BASE)  # must not raise
    assert lint_catalog(_BASE) == []


# --- 18.9 C1: `agentce catalog init` (contracts/P18-18.9.md). ---


def test_catalog_init_writes_a_lint_clean_scaffold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "own-rules-cat"
    code, env = _run(["catalog", "init", str(directory), "--json"], capsys)
    assert code == 0, env
    assert env["family"] == "OWNRULESCAT"[:4]
    for path in _scaffold_files(directory, env["family"]):
        assert path.is_file(), path
    assert sorted(env["files"]) == sorted(
        str(p) for p in _scaffold_files(directory, env["family"])
    )
    assert lint_catalog(directory) == []


def test_catalog_init_fixtures_reproduce_the_claimed_outcomes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three written fixtures reproduce conformant/non-conformant/conformant(applicable=0)
    exactly as ``_lint_case`` checks -- not merely "lint found no problem", the exact outcome class
    each fixture is meant to demonstrate."""
    directory = tmp_path / "cat"
    code, env = _run(["catalog", "init", str(directory), "--json"], capsys)
    assert code == 0, env
    family = env["family"]
    catalog = load_catalog(directory)
    control = catalog.controls[0]
    shape = catalog.shape_for(control)
    assert shape is not None
    domain = DomainBinding.load(directory / "test" / "domain.yaml")

    def _outcome(fixture_name: str) -> tuple[str, int]:
        fixture = directory / "test" / f"{family}-01" / fixture_name
        events = [
            json.loads(line)
            for line in fixture.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        store = build_graph(events, domain=domain)
        result = evaluate_control(
            store,
            shape,
            catalog.shapes,
            control_id=control.id,
            tolerance=control.tolerance,
        )
        return result.outcome, result.applicable

    passed_outcome, passed_applicable = _outcome("passed.jsonl")
    assert passed_outcome == "conformant" and passed_applicable > 0
    failed_outcome, _ = _outcome("failed.jsonl")
    assert failed_outcome == "non-conformant"
    inapplicable_outcome, inapplicable_applicable = _outcome("inapplicable.jsonl")
    assert inapplicable_outcome == "conformant" and inapplicable_applicable == 0


def test_catalog_init_default_id_for_dot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "MyOwnProject"
    project.mkdir()
    monkeypatch.chdir(project)
    code, env = _run(["catalog", "init", ".", "--json"], capsys)
    assert code == 0, env
    assert env["family"] == "MYOW"


def test_catalog_init_default_id_falls_back_for_punctuation_only_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "___"
    code, env = _run(["catalog", "init", str(directory), "--json"], capsys)
    assert code == 0, env
    assert env["family"] == "GEN"


def test_catalog_init_rejects_an_invalid_family(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for bad_id in ("ab", "TOOLONG", "A1"):
        directory = tmp_path / f"cat-{bad_id}"
        code, env = _run(
            ["catalog", "init", str(directory), "--id", bad_id, "--json"], capsys
        )
        assert code == 3, (bad_id, env)
        assert env["error"]["key"] == "catalog.init_family_invalid"
        assert not directory.exists()


@pytest.mark.parametrize(
    "family",
    ["REC", "CND"],
    ids=["base-catalog-family", "overlay-catalog-family"],
)
def test_catalog_init_rejects_a_vendored_family_collision(
    family: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """REC comes from a base catalog, CND from an overlay -- both must collide, since
    `_vendored_control_families` reads every vendored catalog `bundled.vendored_catalogs()` resolves,
    not just the base three."""
    directory = tmp_path / "cat"
    code, env = _run(
        ["catalog", "init", str(directory), "--id", family, "--json"], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.init_family_collision"
    assert not directory.exists()


def test_catalog_init_exists_guard_and_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "cat"
    code, env = _run(
        ["catalog", "init", str(directory), "--id", "GEN", "--json"], capsys
    )
    assert code == 0, env
    before = (directory / "catalog.yaml").read_bytes()

    code, env = _run(
        ["catalog", "init", str(directory), "--id", "GEN", "--json"], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.init_exists"
    assert (directory / "catalog.yaml").read_bytes() == before

    code, env = _run(
        ["catalog", "init", str(directory), "--id", "GEN", "--force", "--json"], capsys
    )
    assert code == 0, env
    assert lint_catalog(directory) == []


def test_catalog_init_title_and_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as _datetime

    import yaml

    class _FrozenDate(_datetime.date):
        @classmethod
        def today(cls) -> "_FrozenDate":
            return cls(2031, 3, 7)

    monkeypatch.setattr(commands, "date", _FrozenDate)
    directory = tmp_path / "cat"
    code, env = _run(
        [
            "catalog",
            "init",
            str(directory),
            "--id",
            "GEN",
            "--title",
            "My Own Rules",
            "--json",
        ],
        capsys,
    )
    assert code == 0, env
    assert env["version"] == "2031.03"
    assert env["title"] == "My Own Rules"
    catalog_doc = yaml.safe_load(
        (directory / "catalog.yaml").read_text(encoding="utf-8")
    )
    assert catalog_doc["version"] == "2031.03"
    assert catalog_doc["title"] == "My Own Rules"
    control_doc = yaml.safe_load(
        (directory / "controls" / "GEN-01.yaml").read_text(encoding="utf-8")
    )
    assert control_doc["version"] == "2031.03"
