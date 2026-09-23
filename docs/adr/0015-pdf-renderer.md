# 0015 — Hand-roll the PDF report renderer instead of a PDF library

Status: accepted
Spec refs: §9

## Context

`agentce assess --emit` gained four new report renderings, one of which is `report.pdf`: a real,
minimally-functional PDF a reader can open in any viewer, clearly labelled a *derived, non-canonical*
rendering (`report.md`/`report.html` remain canonical), dated from the run's own
`manifest.run.started_at`, and reproducible — the same run must produce the same bytes for at least
the parts of the file a reproducibility check can reasonably pin.

The reference engine's dependency tree is a control surface, not an implementation detail (AGENTS.md's
quality gates: "no learned components in any engine or script dependency tree; a dependency-denylist
job enforces it", and every engine dependency is reviewed for advisories and wheel coverage — ADR-0014
records what one pinned cryptography library alone costs in platform coverage). Every general-purpose
Python PDF library (`reportlab`, `weasyprint`, `fpdf`/`fpdf2`, `xhtml2pdf`, `pdfkit`/`wkhtmltopdf`,
`pymupdf`, `borb`) pulls in its own dependency chain, its own advisory history, and in several cases a
native binary (`wkhtmltopdf`, `mupdf`) with its own platform matrix — for one page of static text.

## Decision

Hand-roll `report.pdf` directly, in `report.py`'s `render_pdf`, using only bytes and string
formatting from the standard library. No PDF dependency is added to `pyproject.toml`.

The document is the well-known minimal-PDF pattern: a `%PDF-1.4` header, six indirect objects
(`Catalog`, `Pages`, `Page`, `Font`, a content stream, `Info`), a manually-written cross-reference
table with correct byte offsets, and a `trailer` ending in `%%EOF`. The font is one of the PDF
standard-14 fonts (`/Helvetica`), which every conforming PDF reader must support without an embedded
font program — so the font object is a small, fixed dictionary
(`<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>`) that never
depends on run data and is therefore byte-identical across every run and every report. The content
stream draws the run's verdict, outcome tally, and per-control findings as left-aligned text lines,
opening with an explicit "derived, non-canonical" label rendered as real page text (not a comment, so
it is visible to a reader who never inspects the file's syntax) and the run's `started_at` value
verbatim. The `/CreationDate` in the `Info` dictionary is the same `started_at`, converted to the
PDF-native `D:YYYYMMDDHHMMSSZ` form — the renderer never calls `datetime.now()` or any other
independent wall-clock source; `write_report` reads the clock once, threads that one value into both
`build_manifest` and `render_pdf`.

## Alternatives considered (with why not)

- **`reportlab` or another full PDF library.** Rejected: a real dependency (transitive tree, advisory
  surface, wheel coverage across the platform matrix ADR-0014 already tracks) for output that is one
  page of static text with no layout engine, forms, or embedded fonts needed.
- **Render HTML and shell out to a converter (`wkhtmltopdf`, a headless browser).** Rejected: a native
  binary dependency with its own install story, defeating the "no network, no external process"
  determinism guarantee the rest of the engine holds to (SPEC §9's offline validation, AGENTS.md's
  "tests run with no network" gate).
- **Skip a real PDF and ship a text file named `.pdf`.** Rejected outright by the requirement itself:
  `report.pdf` must open in a real PDF reader — magic bytes glued onto arbitrary text are not a PDF.
- **Embed a real (non-standard-14) font, e.g. an open font file.** Rejected: an embedded font program
  is itself a binary blob with a license and a provenance question, and buys nothing a standard-14 font
  does not already give every conforming reader.

## Consequences (including determinism, portability, performance)

- `report.pdf` is written only when requested (`--emit pdf`, or `report.py`'s `render_pdf` called
  directly); it never joins the engine's original fixed bundle, so no existing artifact set changes.
- No new dependency: `engines/python/pyproject.toml`'s `dependencies` list is unchanged by this work.
- Determinism: the font object's bytes are constant, so they are byte-identical across every run
  (proven in `tests/test_report.py`); the whole file is deterministic given the same assertions,
  counts, and `started_at` (no clock, uuid, or hash-order dependence anywhere in `render_pdf`).
- Portability: this is Python-engine-only for now (the contract that added it is scoped to
  `engines/python`); a TypeScript or Java port, if one is ever needed, follows the same hand-rolled
  approach for the same dependency-surface reason, or is deliberately deferred — either way, that is a
  separate decision made when the port is undertaken.
- Fidelity is deliberately bounded: one page, left-aligned Helvetica text, no pagination for a very
  large assertion set (excess lines run past the page's visible area rather than continuing onto a
  second page). `report.pdf` is a derived convenience rendering, not the canonical report; nothing
  depends on every finding being visible on the rendered page.

## Verification

`engines/python/tests/test_report.py` covers: the file starts with `%PDF-` and ends with `%%EOF`; the
font object's bytes are identical between two independent renders of the same input; the `started_at`
value's digits appear in the file; the visible content stream carries the "derived"/"non-canonical"
label as text, not merely somewhere in the file bytes; and `render_pdf` is reachable end-to-end through
`agentce assess --emit pdf`.
