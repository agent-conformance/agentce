"""Build the documentation and check it offline (SPEC §13.4, eval P5.3).

The reference documentation is generated from the sources so it never drifts from the code: a page
per CLI command from the argument parser, a page per source adapter from its README, and a page per
control family from the base catalog. The living report example is the report the vendored quickstart
project renders (AGENTS.md: rendered outputs come from the corpus). Narrative pages (the index, the
guides, the threat model, the ADRs) are authored by hand and only checked.

Usage::

    python build.py                 # regenerate the reference pages and the example report
    python build.py --check-links   # verify the generated pages are current and every link resolves
    python build.py --check-links --json   # the same, as {"build_clean": bool, "links_clean": bool}

``--check-links`` is clean (prints ``DOCS OK``, exit 0) only when the committed generated pages match a
fresh generation (``build_clean``: the docs are complete and current) and every internal link resolves
(``links_clean``). It opens no socket.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
REPO_ROOT = DOCS.parent
REFERENCE = DOCS / "reference"

# A stable terminal width so argparse help renders identically on every host.
os.environ["COLUMNS"] = "80"


# --- Markdown helpers. ---

_FENCE = re.compile(r"^```")
_LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _slug(heading: str) -> str:
    """GitHub-style heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    text = heading.strip().lower().replace("`", "")
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def _headings(text: str) -> set[str]:
    anchors: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            anchors.add(_slug(match.group(2)))
    return anchors


def _links(text: str) -> list[str]:
    """Every markdown link target outside a fenced code block."""
    targets: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        targets.extend(_LINK.findall(line))
    return targets


# --- Generators (reference pages from the sources). ---


def _command_pages() -> dict[Path, str]:
    from agentce.cli import build_parser

    parser = build_parser()
    sub = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    summaries = {ca.dest: (ca.help or "") for ca in sub._choices_actions}
    pages: dict[Path, str] = {}
    names = sorted(sub.choices)
    for name in names:
        help_text = sub.choices[name].format_help().rstrip()
        summary = summaries.get(name, "").strip()
        pages[REFERENCE / "commands" / f"{name}.md"] = (
            f"# `agentce {name}`\n\n"
            f"{summary[:1].upper() + summary[1:] if summary else ''}.\n\n"
            "```text\n"
            f"{help_text}\n"
            "```\n\n"
            "Exit codes follow the [common CLI scheme](index.md#exit-codes).\n"
        )
    rows = "\n".join(
        f"| [`agentce {name}`]({name}.md) | {summaries.get(name, '').strip()} |"
        for name in names
    )
    pages[REFERENCE / "commands" / "index.md"] = (
        "# CLI commands\n\n"
        "One page per command (SPEC §8.5). Each is generated from the engine's own argument "
        "parser, so the documentation and the tool never disagree.\n\n"
        "| Command | Purpose |\n|---|---|\n"
        f"{rows}\n\n"
        "## Exit codes\n\n"
        "Every command shares one scheme: `0` success with nothing needing action; `1` findings "
        "requiring action; `2` insufficient evidence on a high-severity control (`assess`); `3` an "
        "input, version, or signature-verification error. When several apply the highest is "
        "returned and the `--json` output carries them all.\n"
    )
    return pages


def _adapter_pages() -> dict[Path, str]:
    pages: dict[Path, str] = {}
    names = []
    for adapter in sorted((REPO_ROOT / "adapters").iterdir()):
        readme = adapter / "README.md"
        if not adapter.is_dir() or not readme.is_file():
            continue
        names.append(adapter.name)
        intro = _first_paragraphs(readme.read_text("utf-8"))
        pages[REFERENCE / "adapters" / f"{adapter.name}.md"] = (
            f"# `{adapter.name}` adapter\n\n"
            f"{intro}\n\n"
            f"See the [adapter README](../../../adapters/{adapter.name}/README.md) for the full "
            "source-to-event mapping, fixtures, and the support matrix.\n"
        )
    rows = "\n".join(f"| [`{name}`]({name}.md) |" for name in names)
    pages[REFERENCE / "adapters" / "index.md"] = (
        "# Source adapters\n\n"
        "Each adapter is a pure function from a source export to canonical AgentCE evidence events "
        "(SPEC §12). One page per adapter.\n\n"
        "| Adapter |\n|---|\n"
        f"{rows}\n"
    )
    return pages


def _first_paragraphs(readme: str, limit: int = 2) -> str:
    """The lead paragraphs of a README, up to the first table, list, or subheading."""
    lines = readme.splitlines()
    body = lines[1:] if lines and lines[0].startswith("# ") else lines
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body:
        if line.startswith(("#", "|", "- ", "* ", "```")):
            break
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
            if len(paragraphs) >= limit:
                break
    if current and len(paragraphs) < limit:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def _family_pages() -> dict[Path, str]:
    import yaml

    from agentce.catalog import load_catalog

    catalog_dir = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
    catalog = load_catalog(catalog_dir)
    meta = yaml.safe_load((catalog_dir / "catalog.yaml").read_text("utf-8")) or {}
    families: list[str] = list(meta.get("families", []))
    by_family: dict[str, list] = {family: [] for family in families}
    for control in catalog.controls:
        family = control.id.split("-", 1)[0]
        by_family.setdefault(family, []).append(control)
    pages: dict[Path, str] = {}
    for family in sorted(by_family):
        controls = sorted(by_family[family], key=lambda c: c.id)
        clauses = sorted(
            {
                str(cross.get("clause", ""))
                for control in controls
                for cross in (control.raw.get("crosswalk", []) or [])
                if cross.get("clause")
            }
        )
        rows = "\n".join(
            f"| `{c.id}` | {c.title} | {c.severity} | {c.mode} | {c.rung} |"
            for c in controls
        )
        pages[REFERENCE / "catalog" / f"{family}.md"] = (
            f"# `{family}` control family\n\n"
            f"{len(controls)} control(s) in the EU AI Act base catalog, crosswalked to "
            f"{', '.join(clauses) if clauses else 'the catalog clauses'}. Clause numbers only "
            "are cited; standard text is never reproduced.\n\n"
            "| Control | Title | Severity | Mode | Rung |\n|---|---|---|---|---|\n"
            f"{rows}\n"
        )
    rows = "\n".join(
        f"| [`{family}`]({family}.md) | {len(by_family[family])} |"
        for family in sorted(by_family)
    )
    pages[REFERENCE / "catalog" / "index.md"] = (
        "# Control families\n\n"
        "The EU AI Act base catalog groups its controls into families (SPEC §7). One page per "
        "family, generated from the catalog.\n\n"
        "| Family | Controls |\n|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _reference_index() -> dict[Path, str]:
    return {
        REFERENCE / "index.md": (
            "# Reference\n\n"
            "Generated from the sources by `docs/build.py`.\n\n"
            "- [CLI commands](commands/index.md)\n"
            "- [Source adapters](adapters/index.md)\n"
            "- [Control families](catalog/index.md)\n"
        )
    }


def _example_report() -> dict[Path, str]:
    """Render the living report example from the vendored quickstart project (AX-1)."""
    import contextlib
    import io

    from agentce.cli import main as agentce_main

    with tempfile.TemporaryDirectory() as tmp:
        # Suppress the command's own stdout/stderr so build.py's output stays machine-readable.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = agentce_main(["quickstart", "--out", tmp])
        if code not in (0, 1):
            raise RuntimeError(f"quickstart failed with exit {code}")
        report = (Path(tmp) / "report.md").read_text("utf-8")
    header = (
        "<!-- Generated by docs/build.py from `agentce quickstart`; do not edit by hand. -->\n\n"
        "# Living report example\n\n"
        "This is the report the vendored quickstart project renders end to end with defaults "
        "(SPEC §13.4 AX-1), reproduced here as a worked example. Regenerate it with "
        "`cd docs && python build.py`.\n\n---\n\n"
    )
    return {DOCS / "example-report.md": header + report}


def generate() -> dict[Path, str]:
    """Every generated page, keyed by absolute path."""
    pages: dict[Path, str] = {}
    for part in (
        _command_pages(),
        _adapter_pages(),
        _family_pages(),
        _reference_index(),
        _example_report(),
    ):
        pages.update(part)
    return pages


# --- Build and checks. ---


def write() -> list[Path]:
    pages = generate()
    for path, content in pages.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return sorted(pages)


def check_build() -> list[str]:
    """Warnings if a generated page is missing or stale (the docs are incomplete or out of date)."""
    warnings: list[str] = []
    for path, content in sorted(generate().items()):
        rel = path.relative_to(REPO_ROOT)
        if not path.is_file():
            warnings.append(f"missing generated page: {rel}")
        elif path.read_text("utf-8") != content:
            warnings.append(f"stale generated page (run `python build.py`): {rel}")
    return warnings


def check_links() -> list[str]:
    """Broken internal links across every Markdown file under docs/."""
    broken: list[str] = []
    for md in sorted(DOCS.rglob("*.md")):
        text = md.read_text("utf-8")
        for target in _links(text):
            path_part, _, anchor = target.partition("#")
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if path_part == "":
                dest_text = text  # a same-page anchor
                dest = md
            else:
                dest = (md.parent / path_part).resolve()
                if not dest.exists():
                    broken.append(
                        f"{md.relative_to(REPO_ROOT)} -> {target} (no such path)"
                    )
                    continue
                dest_text = (
                    dest.read_text("utf-8")
                    if dest.is_file() and dest.suffix == ".md"
                    else ""
                )
            if (
                anchor
                and dest.is_file()
                and dest.suffix == ".md"
                and _slug(anchor) not in _headings(dest_text)
            ):
                broken.append(
                    f"{md.relative_to(REPO_ROOT)} -> {target} (no such anchor)"
                )
    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build", description="Build and check the documentation."
    )
    parser.add_argument(
        "--check-links",
        action="store_true",
        help="verify generated pages are current and every link resolves (build nothing)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit {build_clean, links_clean}"
    )
    args = parser.parse_args(argv)

    if not args.check_links:
        written = write()
        print(f"wrote {len(written)} generated pages")
        return 0

    build_warnings = check_build()
    link_warnings = check_links()
    build_clean = not build_warnings
    links_clean = not link_warnings
    if args.json:
        print(
            json.dumps(
                {"build_clean": build_clean, "links_clean": links_clean}, sort_keys=True
            )
        )
    else:
        for warning in build_warnings + link_warnings:
            print(f"DOCS: {warning}", file=sys.stderr)
    if build_clean and links_clean:
        if not args.json:
            print("DOCS OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
