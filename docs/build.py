"""Build the documentation and check it offline (SPEC §13.4, eval P5.3).

The reference documentation is generated from the sources so it never drifts from the code: a page
per CLI command from the argument parser, a page per source adapter from its README, a page per
control family from the base catalog, a page per evidence-model class, and a page per report JSON
Schema. The living report example is the report the vendored quickstart project renders (AGENTS.md:
rendered outputs come from the corpus). Narrative pages (the index, the guides, the threat model, the
ADRs) are authored by hand and only checked.

The same generated content, plus the explanation-quadrant pages (the threat model, the verification
procedure, the message-key catalogue), is also published into the Starlight site under
``website/src/content/docs/{reference,explanation}/`` so a reader of the published site — not only a
checkout of this repository — reaches it (``--publish`` / ``--check-publish``).

Usage::

    python build.py                    # regenerate the reference pages and the example report
    python build.py --check-links      # verify the generated pages are current and every link resolves
    python build.py --check-links --json   # the same, as {"build_clean": bool, "links_clean": bool}
    python build.py --publish          # regenerate the pages published on the website
    python build.py --check-publish    # verify the published pages are current (build nothing)

``--check-links`` is clean (prints ``DOCS OK``, exit 0) only when the committed generated pages match a
fresh generation (``build_clean``: the docs are complete and current) and every internal link resolves
(``links_clean``). ``--check-publish`` is clean (prints ``PUBLISH OK``, exit 0) only when the committed
website pages match a fresh publish. Neither opens a socket.
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
SITE_DOCS = REPO_ROOT / "website" / "src" / "content" / "docs"
SITE_REFERENCE = SITE_DOCS / "reference"
SITE_EXPLANATION = SITE_DOCS / "explanation"

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


def _md_cell(text: str) -> str:
    """Escape a value for a Markdown table cell (a literal `|` would otherwise split it)."""
    return " ".join(text.split()).replace("|", "\\|")


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


def _command_specs() -> list[tuple[str, str, str]]:
    """(name, summary, help text) for every real CLI subcommand, sorted by name."""
    from agentce.cli import build_parser

    parser = build_parser()
    sub = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    summaries = {ca.dest: (ca.help or "") for ca in sub._choices_actions}
    return [
        (
            name,
            summaries.get(name, "").strip(),
            sub.choices[name].format_help().rstrip(),
        )
        for name in sorted(sub.choices)
    ]


def _command_pages() -> dict[Path, str]:
    specs = _command_specs()
    pages: dict[Path, str] = {}
    for name, summary, help_text in specs:
        pages[REFERENCE / "commands" / f"{name}.md"] = (
            f"# `agentce {name}`\n\n"
            f"{summary[:1].upper() + summary[1:] if summary else ''}.\n\n"
            "```text\n"
            f"{help_text}\n"
            "```\n\n"
            "Exit codes follow the [common CLI scheme](index.md#exit-codes).\n"
        )
    rows = "\n".join(
        f"| [`agentce {name}`]({name}.md) | {summary} |" for name, summary, _ in specs
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


def _adapter_specs() -> list[tuple[str, str]]:
    """(name, intro paragraphs) for every adapter with a README, sorted by name."""
    specs: list[tuple[str, str]] = []
    for adapter in sorted((REPO_ROOT / "adapters").iterdir()):
        readme = adapter / "README.md"
        if not adapter.is_dir() or not readme.is_file():
            continue
        specs.append((adapter.name, _first_paragraphs(readme.read_text("utf-8"))))
    return specs


def _adapter_pages() -> dict[Path, str]:
    specs = _adapter_specs()
    pages: dict[Path, str] = {}
    for name, intro in specs:
        pages[REFERENCE / "adapters" / f"{name}.md"] = (
            f"# `{name}` adapter\n\n"
            f"{intro}\n\n"
            f"See the [adapter README](../../../adapters/{name}/README.md) for the full "
            "source-to-event mapping, fixtures, and the support matrix.\n"
        )
    rows = "\n".join(f"| [`{name}`]({name}.md) |" for name, _ in specs)
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


def _family_specs() -> dict[str, list]:
    """Every base-catalog control, grouped by family id."""
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
    return by_family


def _family_pages() -> dict[Path, str]:
    by_family = _family_specs()
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


def _evidence_specs() -> dict[str, dict]:
    """Every class in the evidence model, keyed by name, sorted."""
    import yaml

    model = yaml.safe_load(
        (REPO_ROOT / "spec" / "model" / "agentce-evidence.linkml.yaml").read_text(
            "utf-8"
        )
    )
    return dict(sorted((model.get("classes") or {}).items()))


def _evidence_body_text(body: dict) -> str:
    """The prose and attribute table for one evidence-model class (no heading)."""
    lines: list[str] = []
    description = (body.get("description") or "").strip()
    if description:
        lines.append(f"{description}\n")
    is_a = body.get("is_a")
    if is_a:
        lines.append(f"Extends `{is_a}`.\n")
    if body.get("abstract"):
        lines.append("This is an abstract base class; it is never emitted directly.\n")
    attrs = body.get("attributes") or {}
    if attrs:
        rows = "\n".join(
            f"| `{attr}` | {_md_cell(spec.get('range') or 'string')} | "
            f"{'yes' if spec.get('required') else ''} | {_md_cell(spec.get('description') or '')} |"
            for attr, spec in attrs.items()
        )
        lines.append(
            "| Attribute | Range | Required | Description |\n|---|---|---|---|\n"
            f"{rows}\n"
        )
    return "\n".join(lines)


def _evidence_pages() -> dict[Path, str]:
    classes = _evidence_specs()
    pages: dict[Path, str] = {}
    for name, body in classes.items():
        pages[REFERENCE / "evidence" / f"{name}.md"] = (
            f"# `{name}`\n\n" + _evidence_body_text(body)
        )
    rows = "\n".join(
        f"| [`{name}`]({name}.md) | {'yes' if body.get('abstract') else ''} | "
        f"{_md_cell(body.get('description') or '')} |"
        for name, body in classes.items()
    )
    pages[REFERENCE / "evidence" / "index.md"] = (
        "# Evidence model\n\n"
        "Every class in the AgentCE evidence model (SPEC §6.2), generated from "
        "`spec/model/agentce-evidence.linkml.yaml`. One page per class.\n\n"
        "| Class | Abstract | Description |\n|---|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _report_schema_specs() -> list[tuple[str, dict, str]]:
    """(filename, parsed schema, canonical IRI path) for every report schema, sorted by filename."""
    manifest = json.loads(
        (REPO_ROOT / "website" / "iri-manifest.json").read_text("utf-8")
    )
    by_source = {entry["source"]: entry["path"] for entry in manifest}
    specs: list[tuple[str, dict, str]] = []
    for path in sorted((REPO_ROOT / "spec" / "report").glob("*.schema.json")):
        source_rel = f"spec/report/{path.name}"
        iri_path = by_source.get(source_rel)
        if iri_path is None:
            raise RuntimeError(
                f"{source_rel} is not served at a canonical IRI "
                "(add it to website/iri-manifest.json)"
            )
        specs.append((path.name, json.loads(path.read_text("utf-8")), iri_path))
    return specs


def _schema_type(spec: dict) -> str:
    if "$ref" in spec:
        return f"`{spec['$ref'].rsplit('/', 1)[-1]}`"
    kind = spec.get("type")
    if isinstance(kind, list):
        return " or ".join(kind)
    return kind or "any"


def _schema_body_text(schema: dict, schema_link: str) -> str:
    """The prose and property table for one report schema (no heading)."""
    lines: list[str] = []
    description = (schema.get("description") or "").strip()
    if description:
        lines.append(f"{description}\n")
    lines.append(
        f"[View the schema]({schema_link}) (`{schema.get('$id', schema_link)}`).\n"
    )
    if (
        schema.get("type") == "array"
        and isinstance(schema.get("items"), dict)
        and "$ref" in schema["items"]
    ):
        ref_name = schema["items"]["$ref"].rsplit("/", 1)[-1]
        target = (schema.get("$defs") or {}).get(ref_name, {})
        lines.append(f"Each array element (`{ref_name}`):\n")
        properties = target.get("properties") or {}
    else:
        properties = schema.get("properties") or {}
    if properties:
        rows = "\n".join(
            f"| `{name}` | {_schema_type(spec)} | {_md_cell(spec.get('description') or '')} |"
            for name, spec in properties.items()
        )
        lines.append(f"| Property | Type | Description |\n|---|---|---|\n{rows}\n")
    return "\n".join(lines)


def _report_schema_pages() -> dict[Path, str]:
    specs = _report_schema_specs()
    pages: dict[Path, str] = {}
    for filename, schema, _iri_path in specs:
        slug = filename[: -len(".schema.json")]
        title = schema.get("title") or filename
        rel_link = f"../../../spec/report/{filename}"
        pages[REFERENCE / "report-schemas" / f"{slug}.md"] = (
            f"# `{filename}`\n\n{title}.\n\n" + _schema_body_text(schema, rel_link)
        )
    rows = "\n".join(
        f"| [`{filename}`]({filename[: -len('.schema.json')]}.md) | {_md_cell(schema.get('title') or '')} |"
        for filename, schema, _ in specs
    )
    pages[REFERENCE / "report-schemas" / "index.md"] = (
        "# Report schemas\n\n"
        "One page per JSON Schema in `spec/report/` (SPEC §9), the schemas every engine validates "
        "its output against. Each schema is also served at its own canonical IRI.\n\n"
        "| Schema | Title |\n|---|---|\n"
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
            "- [Evidence model](evidence/index.md)\n"
            "- [Report schemas](report-schemas/index.md)\n"
        )
    }


# --- Publish (the same content, plus the explanation pages, for the Starlight site). ---


def _frontmatter(title: str, description: str) -> str:
    """A Starlight-schema frontmatter block (``docsSchema``: ``title`` required)."""
    import yaml

    front = yaml.safe_dump(
        {"title": title, "description": description},
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )
    return f"---\n{front}---\n\n"


def _site_command_pages() -> dict[Path, str]:
    specs = _command_specs()
    target = SITE_REFERENCE / "commands"
    pages: dict[Path, str] = {}
    for name, summary, help_text in specs:
        description = summary or f"The `agentce {name}` command."
        body = (
            f"{summary[:1].upper() + summary[1:] if summary else ''}.\n\n"
            "```text\n"
            f"{help_text}\n"
            "```\n\n"
            "Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).\n"
        )
        pages[target / f"{name}.md"] = (
            _frontmatter(f"agentce {name}", description) + body
        )
    rows = "\n".join(
        f"| [`agentce {name}`](/reference/commands/{name}/) | {_md_cell(summary)} |"
        for name, summary, _ in specs
    )
    pages[target / "index.md"] = _frontmatter(
        "CLI commands",
        "One page per agentce command, generated from the engine's own argument parser.",
    ) + (
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


def _site_adapter_pages() -> dict[Path, str]:
    specs = _adapter_specs()
    target = SITE_REFERENCE / "adapters"
    pages: dict[Path, str] = {}
    for name, intro in specs:
        github_readme = f"https://github.com/agent-conformance/agentce/blob/main/adapters/{name}/README.md"
        body = (
            f"{intro}\n\n"
            f"See the [adapter README]({github_readme}) for the full source-to-event mapping, "
            "fixtures, and the support matrix.\n"
        )
        pages[target / f"{name}.md"] = (
            _frontmatter(f"{name} adapter", _md_cell(intro)[:150]) + body
        )
    rows = "\n".join(
        f"| [`{name}`](/reference/adapters/{name}/) |" for name, _ in specs
    )
    pages[target / "index.md"] = _frontmatter(
        "Source adapters",
        "Each adapter is a pure function from a source export to canonical AgentCE evidence events.",
    ) + (
        "Each adapter is a pure function from a source export to canonical AgentCE evidence events "
        "(SPEC §12). One page per adapter.\n\n"
        "| Adapter |\n|---|\n"
        f"{rows}\n"
    )
    return pages


def _site_catalog_pages() -> dict[Path, str]:
    by_family = _family_specs()
    target = SITE_REFERENCE / "catalog"
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
            f"| `{c.id}` | {_md_cell(c.title)} | {c.severity} | {c.mode} | {c.rung} |"
            for c in controls
        )
        body = (
            f"{len(controls)} control(s) in the EU AI Act base catalog, crosswalked to "
            f"{', '.join(clauses) if clauses else 'the catalog clauses'}. Clause numbers only "
            "are cited; standard text is never reproduced.\n\n"
            "| Control | Title | Severity | Mode | Rung |\n|---|---|---|---|---|\n"
            f"{rows}\n"
        )
        pages[target / f"{family}.md"] = (
            _frontmatter(
                f"{family} control family",
                f"{len(controls)} control(s) in the {family} family.",
            )
            + body
        )
    rows = "\n".join(
        # Starlight lowercases every route slug regardless of the source file's own case.
        f"| [`{family}`](/reference/catalog/{family.lower()}/) | {len(by_family[family])} |"
        for family in sorted(by_family)
    )
    pages[target / "index.md"] = _frontmatter(
        "Control families", "The EU AI Act base catalog grouped into families."
    ) + (
        "The EU AI Act base catalog groups its controls into families (SPEC §7). One page per "
        "family, generated from the catalog.\n\n"
        "| Family | Controls |\n|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _site_evidence_pages() -> dict[Path, str]:
    classes = _evidence_specs()
    target = SITE_REFERENCE / "evidence"
    pages: dict[Path, str] = {}
    for name, body in classes.items():
        description = (
            body.get("description") or f"The `{name}` evidence-model class."
        ).strip()
        pages[target / f"{name}.md"] = _frontmatter(
            name, _md_cell(description)[:150]
        ) + _evidence_body_text(body)
    rows = "\n".join(
        # Starlight lowercases every route slug regardless of the source file's own case.
        f"| [`{name}`](/reference/evidence/{name.lower()}/) | {'yes' if body.get('abstract') else ''} | "
        f"{_md_cell(body.get('description') or '')} |"
        for name, body in classes.items()
    )
    pages[target / "index.md"] = _frontmatter(
        "Evidence model",
        "Every class in the AgentCE evidence model, generated from the LinkML source.",
    ) + (
        "Every class in the AgentCE evidence model (SPEC §6.2), generated from "
        "`spec/model/agentce-evidence.linkml.yaml`. One page per class.\n\n"
        "| Class | Abstract | Description |\n|---|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _site_report_schema_pages() -> dict[Path, str]:
    specs = _report_schema_specs()
    target = SITE_REFERENCE / "report-schemas"
    pages: dict[Path, str] = {}
    for filename, schema, iri_path in specs:
        slug = filename[: -len(".schema.json")]
        title = schema.get("title") or filename
        body = f"{title}.\n\n" + _schema_body_text(schema, iri_path)
        description = _md_cell(schema.get("description") or title)[:150]
        pages[target / f"{slug}.md"] = _frontmatter(title, description) + body
    rows = "\n".join(
        f"| [`{filename}`](/reference/report-schemas/{filename[: -len('.schema.json')]}/) | "
        f"{_md_cell(schema.get('title') or '')} |"
        for filename, schema, _ in specs
    )
    pages[target / "index.md"] = _frontmatter(
        "Report schemas", "Every JSON Schema AgentCE's outputs validate against."
    ) + (
        "One page per JSON Schema in `spec/report/` (SPEC §9), the schemas every engine validates "
        "its output against. Each schema is also served at its own canonical IRI.\n\n"
        "| Schema | Title |\n|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _site_reference_index() -> dict[Path, str]:
    return {
        SITE_REFERENCE / "index.md": _frontmatter(
            "Reference",
            "Generated from the sources: CLI commands, adapters, control families, the evidence "
            "model, and report schemas.",
        )
        + (
            "Generated from the sources, so the documentation and the code never disagree.\n\n"
            "- [CLI commands](/reference/commands/) — one page per command.\n"
            "- [Source adapters](/reference/adapters/) — one page per adapter.\n"
            "- [Control families](/reference/catalog/) — one page per family of the EU AI Act base catalog.\n"
            "- [Evidence model](/reference/evidence/) — one page per class in the evidence schema.\n"
            "- [Report schemas](/reference/report-schemas/) — one page per JSON Schema an output validates against.\n"
        )
    }


def _site_explanation_pages() -> dict[Path, str]:
    """Publish the understanding-oriented pages authored under docs/ (unchanged, minus their H1)."""
    sources = {
        "threat-model": DOCS / "threat-model.md",
        "verification": DOCS / "verification.md",
        "errors": DOCS / "errors.md",
    }
    # The only cross-links between these pages, rewritten to the site's absolute routes.
    rewrites = {
        "(verification.md)": "(/explanation/verification/)",
        "(./threat-model.md)": "(/explanation/threat-model/)",
    }
    pages: dict[Path, str] = {}
    titles: dict[str, str] = {}
    for slug, path in sources.items():
        text = path.read_text("utf-8")
        lines = text.splitlines()
        if not lines or not lines[0].startswith("# "):
            raise RuntimeError(f"{path} must start with a top-level heading to publish")
        title = lines[0][2:].strip()
        titles[slug] = title
        body = "\n".join(lines[1:]).lstrip("\n") + "\n"
        for old, new in rewrites.items():
            body = body.replace(old, new)
        description = _md_cell(_first_paragraphs(text, limit=1))[:150]
        pages[SITE_EXPLANATION / f"{slug}.md"] = _frontmatter(title, description) + body
    rows = "\n".join(
        f"| [{title}](/explanation/{slug}/) |" for slug, title in titles.items()
    )
    pages[SITE_EXPLANATION / "index.md"] = _frontmatter(
        "Explanation",
        "Understanding-oriented material: the threat model, the verification procedure, and the "
        "message-key catalogue.",
    ) + (
        "Understanding-oriented material published from `docs/` (SPEC §13.4).\n\n"
        "| Page |\n|---|\n"
        f"{rows}\n"
    )
    return pages


def generate_site() -> dict[Path, str]:
    """Every page published to the website, keyed by absolute path."""
    pages: dict[Path, str] = {}
    for part in (
        _site_command_pages(),
        _site_adapter_pages(),
        _site_catalog_pages(),
        _site_evidence_pages(),
        _site_report_schema_pages(),
        _site_reference_index(),
        _site_explanation_pages(),
    ):
        pages.update(part)
    return pages


def write_site() -> list[Path]:
    pages = generate_site()
    for path, content in pages.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return sorted(pages)


def check_site() -> list[str]:
    """Warnings if a published page is missing or stale (the website copy is incomplete or out of date)."""
    warnings: list[str] = []
    for path, content in sorted(generate_site().items()):
        rel = path.relative_to(REPO_ROOT)
        if not path.is_file():
            warnings.append(f"missing published page: {rel}")
        elif path.read_text("utf-8") != content:
            warnings.append(
                f"stale published page (run `python build.py --publish`): {rel}"
            )
    return warnings


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
        _evidence_pages(),
        _report_schema_pages(),
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
        "--publish",
        action="store_true",
        help="write the reference and explanation pages published on the website (build nothing else)",
    )
    parser.add_argument(
        "--check-publish",
        action="store_true",
        help="verify the published website pages are current (build nothing)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit {build_clean, links_clean}"
    )
    args = parser.parse_args(argv)

    if args.publish:
        written = write_site()
        print(f"published {len(written)} pages")
        return 0

    if args.check_publish:
        publish_warnings = check_site()
        for warning in publish_warnings:
            print(f"DOCS: {warning}", file=sys.stderr)
        if not publish_warnings:
            print("PUBLISH OK")
            return 0
        return 1

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
