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
