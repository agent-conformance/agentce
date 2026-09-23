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
from typing import Any

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


def _command_rows() -> list[tuple[str, str, str]]:
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
    entries = _command_rows()
    pages: dict[Path, str] = {}
    for name, summary, help_text in entries:
        pages[REFERENCE / "commands" / f"{name}.md"] = (
            f"# `agentce {name}`\n\n"
            f"{summary[:1].upper() + summary[1:] if summary else ''}.\n\n"
            "```text\n"
            f"{help_text}\n"
            "```\n\n"
            "Exit codes follow the [common CLI scheme](index.md#exit-codes).\n"
        )
    rows = "\n".join(
        f"| [`agentce {name}`]({name}.md) | {summary} |" for name, summary, _ in entries
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


def _adapter_rows() -> list[tuple[str, str]]:
    """(name, intro paragraphs) for every adapter with a README, sorted by name."""
    entries: list[tuple[str, str]] = []
    for adapter in sorted((REPO_ROOT / "adapters").iterdir()):
        readme = adapter / "README.md"
        if not adapter.is_dir() or not readme.is_file():
            continue
        entries.append((adapter.name, _first_paragraphs(readme.read_text("utf-8"))))
    return entries


def _adapter_pages() -> dict[Path, str]:
    entries = _adapter_rows()
    pages: dict[Path, str] = {}
    for name, intro in entries:
        pages[REFERENCE / "adapters" / f"{name}.md"] = (
            f"# `{name}` adapter\n\n"
            f"{intro}\n\n"
            f"See the [adapter README](../../../adapters/{name}/README.md) for the full "
            "source-to-event mapping, fixtures, and the support matrix.\n"
        )
    rows = "\n".join(f"| [`{name}`]({name}.md) |" for name, _ in entries)
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


def _family_groups() -> dict[str, list]:
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
    by_family = _family_groups()
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


def _evidence_classes() -> dict[str, dict]:
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
    classes = _evidence_classes()
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


def _report_schema_rows() -> list[tuple[str, dict, str]]:
    """(filename, parsed schema, canonical IRI path) for every report schema, sorted by filename."""
    manifest = json.loads(
        (REPO_ROOT / "website" / "iri-manifest.json").read_text("utf-8")
    )
    by_source = {entry["source"]: entry["path"] for entry in manifest}
    entries: list[tuple[str, dict, str]] = []
    for path in sorted((REPO_ROOT / "spec" / "report").glob("*.schema.json")):
        source_rel = f"spec/report/{path.name}"
        iri_path = by_source.get(source_rel)
        if iri_path is None:
            raise RuntimeError(
                f"{source_rel} is not served at a canonical IRI "
                "(add it to website/iri-manifest.json)"
            )
        entries.append((path.name, json.loads(path.read_text("utf-8")), iri_path))
    return entries


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
    entries = _report_schema_rows()
    pages: dict[Path, str] = {}
    for filename, schema, _iri_path in entries:
        slug = filename[: -len(".schema.json")]
        title = schema.get("title") or filename
        rel_link = f"../../../spec/report/{filename}"
        pages[REFERENCE / "report-schemas" / f"{slug}.md"] = (
            f"# `{filename}`\n\n{title}.\n\n" + _schema_body_text(schema, rel_link)
        )
    rows = "\n".join(
        f"| [`{filename}`]({filename[: -len('.schema.json')]}.md) | {_md_cell(schema.get('title') or '')} |"
        for filename, schema, _ in entries
    )
    pages[REFERENCE / "report-schemas" / "index.md"] = (
        "# Report schemas\n\n"
        "One page per JSON Schema in `spec/report/` (SPEC §9), the schemas every engine validates "
        "its output against. Each schema is also served at its own canonical IRI.\n\n"
        "| Schema | Title |\n|---|---|\n"
        f"{rows}\n"
    )
    return pages


#: Every control catalog directory this documentation covers, keyed by a short id used in filenames
#: and headings: the EU AI Act base catalog plus its four sector/conduct overlays (SPEC §7.3).
_CATALOG_DIRS = {
    "eu-ai-act": REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act",
    "conduct": REPO_ROOT / "spec" / "catalogs" / "overlays" / "conduct",
    "employment": REPO_ROOT / "spec" / "catalogs" / "overlays" / "employment",
    "finance": REPO_ROOT / "spec" / "catalogs" / "overlays" / "finance",
    "insurance": REPO_ROOT / "spec" / "catalogs" / "overlays" / "insurance",
}


def _control_rows() -> list[tuple[str, str, str, Any]]:
    """(catalog key, catalog title, catalog kind, control) for every control in the base catalog and
    every overlay (SPEC §7.3), sorted by control id. Every real control id in the standard, once."""
    import yaml

    from agentce.catalog import load_catalog

    entries: list[tuple[str, str, str, Any]] = []
    for key, directory in _CATALOG_DIRS.items():
        meta = yaml.safe_load((directory / "catalog.yaml").read_text("utf-8")) or {}
        catalog = load_catalog(directory)
        title = str(meta.get("title") or key)
        kind = str(meta.get("kind") or "base")
        for control in catalog.controls:
            entries.append((key, title, kind, control))
    return sorted(entries, key=lambda entry: entry[3].id)


def _control_body_text(catalog_title: str, catalog_kind: str, control) -> str:
    """The prose and field table for one control (no heading)."""
    clauses = sorted(
        {
            str(cross.get("clause", ""))
            for cross in (control.raw.get("crosswalk", []) or [])
            if cross.get("clause")
        }
    )
    lines = [
        f"{control.title}.\n",
        "| Field | Value |\n|---|---|\n"
        f"| Severity | {control.severity} |\n"
        f"| Mode | {control.mode} |\n"
        f"| Rung | {control.rung} |\n"
        f"| Catalog | {catalog_title} ({catalog_kind}) |\n",
    ]
    if clauses:
        lines.append(
            f"Crosswalked to clause(s) {', '.join(clauses)}. Clause numbers only are cited; "
            "standard text is never reproduced.\n"
        )
    return "\n".join(lines)


def _control_pages() -> dict[Path, str]:
    entries = _control_rows()
    pages: dict[Path, str] = {}
    by_catalog: dict[str, list] = {}
    for key, title, kind, control in entries:
        slug = control.id.lower()
        pages[REFERENCE / "controls" / f"{slug}.md"] = (
            f"# {control.id}\n\n" + _control_body_text(title, kind, control)
        )
        by_catalog.setdefault(key, []).append((title, control))
    sections: list[str] = []
    for key in sorted(by_catalog):
        title = by_catalog[key][0][0]
        controls = sorted((c for _t, c in by_catalog[key]), key=lambda c: c.id)
        rows = "\n".join(
            f"| [`{c.id}`]({c.id.lower()}.md) | {c.title} | {c.severity} | {c.mode} | {c.rung} |"
            for c in controls
        )
        sections.append(
            f"## {title}\n\n"
            "| Control | Title | Severity | Mode | Rung |\n|---|---|---|---|---|\n"
            f"{rows}\n"
        )
    pages[REFERENCE / "controls" / "index.md"] = (
        "# Controls\n\n"
        f"Every control across the EU AI Act base catalog and its overlays (SPEC §7.3), "
        f"{len(entries)} in total, generated from the catalog YAML. One page per control, grouped "
        "here by catalog.\n\n" + "\n".join(sections)
    )
    return pages


def _extra_iri_rows() -> list[dict]:
    """The canonical IRI manifest entries not already published with their own title'd page by
    ``_report_schema_pages`` (which covers every schema under ``spec/report/``): the JSON-LD context,
    the RDF vocabulary, and every schema outside ``spec/report/``."""
    manifest = json.loads(
        (REPO_ROOT / "website" / "iri-manifest.json").read_text("utf-8")
    )
    report_schema_names = {
        p.name for p in (REPO_ROOT / "spec" / "report").glob("*.schema.json")
    }
    return [
        entry
        for entry in manifest
        if not (
            entry["source"].startswith("spec/report/")
            and Path(entry["source"]).name in report_schema_names
        )
    ]


def _iri_slug(path: str) -> str:
    slug = path.strip("/").replace("/", "-")
    if slug.endswith(".schema.json"):
        slug = slug[: -len(".schema.json")]
    return slug.lower()


def _iri_title(entry: dict) -> str:
    """The IRI's own path, or — for a schema with a ``title`` — that schema's title."""
    if entry["source"].endswith(".schema.json"):
        schema = json.loads((REPO_ROOT / entry["source"]).read_text("utf-8"))
        if schema.get("title"):
            return str(schema["title"])
    return entry["path"]


def _iri_body_text(entry: dict, schema_link: str) -> str:
    """The prose (and, for a schema, the property table) for one non-report canonical IRI."""
    canonical = f"https://agent-conformance.org{entry['path']}"
    lines = [
        f"Served at its canonical IRI: `{entry['path']}` (`{entry['content_type']}`).\n"
    ]
    if entry["source"].endswith(".schema.json"):
        schema = json.loads((REPO_ROOT / entry["source"]).read_text("utf-8"))
        description = (schema.get("description") or "").strip()
        if description:
            lines.append(f"{description}\n")
        lines.append(_schema_body_text(schema, schema_link))
    elif entry["source"].endswith(".ttl"):
        lines.append(f"[View the vocabulary]({schema_link}).\n")
        lines.append(
            "The RDF vocabulary (OWL/RDFS) AgentCE evidence graphs are profiled against: a PROV-O "
            "profile plus the glue relations SPEC §6.3 defines because no existing vocabulary "
            "carries them.\n"
        )
    else:
        lines.append(f"[View the context]({schema_link}).\n")
        lines.append(
            "The JSON-LD `@context` that maps AgentCE evidence-model field names onto the RDF "
            "vocabulary's IRIs, so an evidence bundle can be read as linked data (SPEC §6.3).\n"
        )
    lines.append(f"[View at its canonical IRI]({canonical}).\n")
    return "\n".join(lines)


def _iri_explorer_rows() -> list[tuple[dict, str, str]]:
    """(manifest entry, page category, slug) for every one of the 19 canonical IRIs: ``iris`` for the
    pages this module generates fresh, ``report-schemas`` for the ones already published with a
    title'd page by ``_report_schema_pages``."""
    manifest = json.loads(
        (REPO_ROOT / "website" / "iri-manifest.json").read_text("utf-8")
    )
    report_schema_names = {
        p.name for p in (REPO_ROOT / "spec" / "report").glob("*.schema.json")
    }
    rows: list[tuple[dict, str, str]] = []
    for entry in manifest:
        source_name = Path(entry["source"]).name
        if (
            entry["source"].startswith("spec/report/")
            and source_name in report_schema_names
        ):
            rows.append((entry, "report-schemas", source_name[: -len(".schema.json")]))
        else:
            rows.append((entry, "iris", _iri_slug(entry["path"])))
    return rows


def _iri_pages() -> dict[Path, str]:
    extra = _extra_iri_rows()
    pages: dict[Path, str] = {}
    for entry in extra:
        slug = _iri_slug(entry["path"])
        title = _iri_title(entry)
        rel_link = f"../../../{entry['source']}"
        pages[REFERENCE / "iris" / f"{slug}.md"] = f"# {title}\n\n" + _iri_body_text(
            entry, rel_link
        )
    explorer = _iri_explorer_rows()
    rows = "\n".join(
        f"| `{entry['path']}` | "
        f"[{_iri_title(entry)}]({'../report-schemas/' + slug + '.md' if category == 'report-schemas' else slug + '.md'}) | "
        f"{entry['content_type']} |"
        for entry, category, slug in explorer
    )
    pages[REFERENCE / "iris" / "index.md"] = (
        "# Canonical IRIs\n\n"
        f"Every canonical, dereferenceable IRI the specification defines (SPEC §6, §9; "
        f"`website/iri-manifest.json`), {len(explorer)} in total: the JSON-LD context, the RDF "
        "vocabulary, and every JSON Schema an engine or a report validates against. Each is served "
        "byte-identically at its own canonical path and, here, linked to a human-readable page.\n\n"
        "| IRI | Page | Content type |\n|---|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _glossary_terms() -> list[dict[str, str]]:
    """The canonical glossary term list (SPEC Appendix E), from ``spec/methodology/glossary.yaml``."""
    import yaml

    data = (
        yaml.safe_load(
            (REPO_ROOT / "spec" / "methodology" / "glossary.yaml").read_text("utf-8")
        )
        or {}
    )
    return list(data.get("terms", []))


def _glossary_dl(terms: list[dict[str, str]]) -> str:
    """A real HTML definition list: Markdown has no definition-list syntax, and Starlight's renderer
    passes a raw HTML block through, so the built page carries literal ``<dt>``/``<dd>`` tags."""
    rows = "\n".join(f"<dt>{t['term']}</dt>\n<dd>{t['definition']}</dd>" for t in terms)
    return f"<dl>\n{rows}\n</dl>\n"


def _glossary_body_text() -> str:
    return (
        "Canonical AgentCE terminology (SPEC Appendix E), generated from "
        "`spec/methodology/glossary.yaml` so the published glossary cannot drift from the "
        "specification's own term list.\n\n" + _glossary_dl(_glossary_terms())
    )


def _glossary_pages() -> dict[Path, str]:
    return {REFERENCE / "glossary.md": "# Glossary\n\n" + _glossary_body_text()}


def _reference_index() -> dict[Path, str]:
    return {
        REFERENCE / "index.md": (
            "# Reference\n\n"
            "Generated from the sources by `docs/build.py`.\n\n"
            "- [CLI commands](commands/index.md)\n"
            "- [Source adapters](adapters/index.md)\n"
            "- [Control families](catalog/index.md)\n"
            "- [Controls](controls/index.md)\n"
            "- [Evidence model](evidence/index.md)\n"
            "- [Report schemas](report-schemas/index.md)\n"
            "- [Canonical IRIs](iris/index.md)\n"
            "- [Glossary](glossary.md)\n"
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
    entries = _command_rows()
    target = SITE_REFERENCE / "commands"
    pages: dict[Path, str] = {}
    for name, summary, help_text in entries:
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
        for name, summary, _ in entries
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
    entries = _adapter_rows()
    target = SITE_REFERENCE / "adapters"
    pages: dict[Path, str] = {}
    for name, intro in entries:
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
        f"| [`{name}`](/reference/adapters/{name}/) |" for name, _ in entries
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
    by_family = _family_groups()
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
    classes = _evidence_classes()
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
    entries = _report_schema_rows()
    target = SITE_REFERENCE / "report-schemas"
    pages: dict[Path, str] = {}
    for filename, schema, iri_path in entries:
        slug = filename[: -len(".schema.json")]
        title = schema.get("title") or filename
        body = f"{title}.\n\n" + _schema_body_text(schema, iri_path)
        description = _md_cell(schema.get("description") or title)[:150]
        pages[target / f"{slug}.md"] = _frontmatter(title, description) + body
    rows = "\n".join(
        f"| [`{filename}`](/reference/report-schemas/{filename[: -len('.schema.json')]}/) | "
        f"{_md_cell(schema.get('title') or '')} |"
        for filename, schema, _ in entries
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


def _site_control_pages() -> dict[Path, str]:
    entries = _control_rows()
    target = SITE_REFERENCE / "controls"
    pages: dict[Path, str] = {}
    by_catalog: dict[str, list] = {}
    for key, title, kind, control in entries:
        slug = control.id.lower()
        body = _control_body_text(title, kind, control)
        pages[target / f"{slug}.md"] = (
            _frontmatter(control.id, _md_cell(f"{control.title} ({title}).")[:150])
            + body
        )
        by_catalog.setdefault(key, []).append((title, control))
    sections: list[str] = []
    for key in sorted(by_catalog):
        title = by_catalog[key][0][0]
        controls = sorted((c for _t, c in by_catalog[key]), key=lambda c: c.id)
        rows = "\n".join(
            # Starlight lowercases every route slug regardless of the source file's own case.
            f"| [`{c.id}`](/reference/controls/{c.id.lower()}/) | {_md_cell(c.title)} | {c.severity} | {c.mode} | {c.rung} |"
            for c in controls
        )
        sections.append(
            f"## {title}\n\n"
            "| Control | Title | Severity | Mode | Rung |\n|---|---|---|---|---|\n"
            f"{rows}\n"
        )
    pages[target / "index.md"] = _frontmatter(
        "Controls",
        f"Every control across the EU AI Act base catalog and its overlays, {len(entries)} in total.",
    ) + (
        f"Every control across the EU AI Act base catalog and its overlays (SPEC §7.3), "
        f"{len(entries)} in total, generated from the catalog YAML. One page per control, grouped "
        "here by catalog.\n\n" + "\n".join(sections)
    )
    return pages


def _site_iri_pages() -> dict[Path, str]:
    extra = _extra_iri_rows()
    target = SITE_REFERENCE / "iris"
    pages: dict[Path, str] = {}
    for entry in extra:
        slug = _iri_slug(entry["path"])
        title = _iri_title(entry)
        canonical = f"https://agent-conformance.org{entry['path']}"
        pages[target / f"{slug}.md"] = _frontmatter(
            title, _md_cell(f"Served at its canonical IRI, {entry['path']}.")[:150]
        ) + _iri_body_text(entry, canonical)
    explorer = _iri_explorer_rows()
    rows = "\n".join(
        f"| `{entry['path']}` | "
        f"[{_iri_title(entry)}](/reference/{category}/{slug}/) | "
        f"{entry['content_type']} |"
        for entry, category, slug in explorer
    )
    pages[target / "index.md"] = _frontmatter(
        "Canonical IRIs",
        f"Every one of the {len(explorer)} canonical, dereferenceable IRIs the specification defines.",
    ) + (
        f"Every canonical, dereferenceable IRI the specification defines (SPEC §6, §9; "
        f"`website/iri-manifest.json`), {len(explorer)} in total: the JSON-LD context, the RDF "
        "vocabulary, and every JSON Schema an engine or a report validates against. Each is served "
        "byte-identically at its own canonical path and, here, linked to a human-readable page.\n\n"
        "| IRI | Page | Content type |\n|---|---|---|\n"
        f"{rows}\n"
    )
    return pages


def _site_glossary_pages() -> dict[Path, str]:
    return {
        SITE_REFERENCE / "glossary.md": _frontmatter(
            "Glossary",
            "Canonical AgentCE terminology, generated from the specification's own term list.",
        )
        + _glossary_body_text()
    }


def _site_reference_index() -> dict[Path, str]:
    return {
        SITE_REFERENCE / "index.md": _frontmatter(
            "Reference",
            "Generated from the sources: CLI commands, adapters, control families, controls, the "
            "evidence model, report schemas, canonical IRIs, and the glossary.",
        )
        + (
            "Generated from the sources, so the documentation and the code never disagree.\n\n"
            "- [CLI commands](/reference/commands/) — one page per command.\n"
            "- [Source adapters](/reference/adapters/) — one page per adapter.\n"
            "- [Control families](/reference/catalog/) — one page per family of the EU AI Act base catalog.\n"
            "- [Controls](/reference/controls/) — one page per control across the base catalog and its overlays.\n"
            "- [Evidence model](/reference/evidence/) — one page per class in the evidence schema.\n"
            "- [Report schemas](/reference/report-schemas/) — one page per JSON Schema an output validates against.\n"
            "- [Canonical IRIs](/reference/iris/) — every canonical IRI the specification defines, linked to a human page.\n"
            "- [Glossary](/reference/glossary/) — canonical AgentCE terminology.\n"
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
        _site_control_pages(),
        _site_evidence_pages(),
        _site_report_schema_pages(),
        _site_iri_pages(),
        _site_glossary_pages(),
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
        _control_pages(),
        _evidence_pages(),
        _report_schema_pages(),
        _iri_pages(),
        _glossary_pages(),
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
