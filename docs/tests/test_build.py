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
