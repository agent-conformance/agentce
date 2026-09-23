"""The documentation build and offline link check (SPEC §13.4, P5.3)."""

from __future__ import annotations

import build


def test_generated_pages_are_current() -> None:
    """The committed reference pages and example report match a fresh generation (build_clean)."""
    assert build.check_build() == []


def test_all_internal_links_resolve() -> None:
    assert build.check_links() == []


def test_a_page_exists_for_every_command_adapter_and_family() -> None:
    pages = {p.relative_to(build.DOCS).as_posix() for p in build.generate()}
    from agentce.cli import build_parser

    import argparse

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for command in sub.choices:
        assert f"reference/commands/{command}.md" in pages
    for adapter in (build.REPO_ROOT / "adapters").iterdir():
        if (adapter / "README.md").is_file():
            assert f"reference/adapters/{adapter.name}.md" in pages
    assert any(p.startswith("reference/catalog/") for p in pages)
    assert "example-report.md" in pages


def test_a_page_exists_for_every_evidence_class_and_report_schema() -> None:
    pages = {p.relative_to(build.DOCS).as_posix() for p in build.generate()}
    for name in build._evidence_classes():
        assert f"reference/evidence/{name}.md" in pages
    for path in (build.REPO_ROOT / "spec" / "report").glob("*.schema.json"):
        slug = path.name[: -len(".schema.json")]
        assert f"reference/report-schemas/{slug}.md" in pages


def test_a_page_exists_for_every_control_across_every_catalog() -> None:
    pages = {p.relative_to(build.DOCS).as_posix() for p in build.generate()}
    rows = build._control_rows()
    assert (
        len(rows) == 60
    )  # 49 base + 8 conduct + 1 each of employment/finance/insurance
    for _key, _title, _kind, control in rows:
        assert f"reference/controls/{control.id.lower()}.md" in pages
    assert "reference/controls/index.md" in pages


def test_control_page_h1_is_exactly_the_control_id() -> None:
    pages = build.generate()
    for _key, _title, _kind, control in build._control_rows():
        path = build.REFERENCE / "controls" / f"{control.id.lower()}.md"
        assert pages[path].startswith(f"# {control.id}\n\n")


def test_a_page_exists_for_every_canonical_iri_not_already_a_report_schema() -> None:
    generated = build.generate()
    pages = {p.relative_to(build.DOCS).as_posix() for p in generated}
    for entry in build._extra_iri_rows():
        slug = build._iri_slug(entry["path"])
        assert f"reference/iris/{slug}.md" in pages
    assert "reference/iris/index.md" in pages
    # Every one of the 19 canonical IRI paths appears verbatim in the explorer page.
    explorer = generated[build.REFERENCE / "iris" / "index.md"]
    manifest = build.json.loads(
        (build.REPO_ROOT / "website" / "iri-manifest.json").read_text("utf-8")
    )
    assert len(manifest) == 19
    for entry in manifest:
        assert entry["path"] in explorer


def test_glossary_page_has_a_definition_list_for_every_term() -> None:
    pages = build.generate()
    glossary = pages[build.REFERENCE / "glossary.md"]
    assert glossary.startswith("# Glossary\n\n")
    terms = build._glossary_terms()
    assert len(terms) == 10
    for term in terms:
        assert f"<dt>{term['term']}</dt>" in glossary
        assert f"<dd>{term['definition']}</dd>" in glossary


def test_published_pages_are_current() -> None:
    """The committed website pages match a fresh publish."""
    assert build.check_site() == []


def test_published_pages_cover_every_command_control_class_and_schema() -> None:
    pages = {p.relative_to(build.SITE_DOCS).as_posix() for p in build.generate_site()}
    for name, _summary, _help in build._command_rows():
        assert f"reference/commands/{name}.md" in pages
    for name in build._evidence_classes():
        assert f"reference/evidence/{name}.md" in pages
    for filename, _schema, _iri in build._report_schema_rows():
        slug = filename[: -len(".schema.json")]
        assert f"reference/report-schemas/{slug}.md" in pages
    for family in build._family_groups():
        assert f"reference/catalog/{family}.md" in pages
    for _key, _title, _kind, control in build._control_rows():
        assert f"reference/controls/{control.id.lower()}.md" in pages
    for entry in build._extra_iri_rows():
        assert f"reference/iris/{build._iri_slug(entry['path'])}.md" in pages
    assert "reference/glossary.md" in pages
    for slug in ("threat-model", "verification", "errors"):
        assert f"explanation/{slug}.md" in pages


def test_published_pages_have_a_starlight_title_and_a_single_top_level_heading() -> (
    None
):
    for path, content in build.generate_site().items():
        assert content.startswith("---\ntitle:"), (
            f"{path} is missing Starlight frontmatter"
        )
        body = content.split("---\n", 2)[2]
        assert body.count("\n# ") == 0 and not body.startswith("# "), (
            f"{path} has a body-level top-level heading, which would duplicate Starlight's own"
        )


def test_published_control_page_title_is_exactly_the_control_id() -> None:
    site_pages = build.generate_site()
    for _key, _title, _kind, control in build._control_rows():
        path = build.SITE_REFERENCE / "controls" / f"{control.id.lower()}.md"
        assert site_pages[path].startswith(f"---\ntitle: {control.id}\n")


def test_published_glossary_page_has_a_definition_list_for_every_term() -> None:
    site_pages = build.generate_site()
    glossary = site_pages[build.SITE_REFERENCE / "glossary.md"]
    assert glossary.startswith("---\ntitle: Glossary\n")
    for term in build._glossary_terms():
        assert f"<dt>{term['term']}</dt>" in glossary
        assert f"<dd>{term['definition']}</dd>" in glossary


def test_link_checker_is_not_vacuous() -> None:
    """The anchor slugger and link scanner behave, so a clean result means something."""
    assert build._slug("`agentce verify`") == "agentce-verify"
    assert build._slug("Exit codes") == "exit-codes"
    assert build._links("see [a](x.md) and [b](y.md#z)") == ["x.md", "y.md#z"]
    # A link inside a fenced block is not a link.
    assert build._links("```\n[a](x.md)\n```") == []
    assert "exit-codes" in build._headings("## Exit codes\n")


def test_example_report_is_the_quickstart_report() -> None:
    example = (build.DOCS / "example-report.md").read_text("utf-8")
    assert "Living report example" in example
    assert "quickstart" in example.lower()
