# Corpus dataset versions

This file pins the corpus dataset revision and manifest digest each catalog version was validated
against (SPEC §11.7). Engines, the Engine Conformance Suite, and implementation reports reference the
corpus by these pins — never by branch name — so a conformance claim names an exact, reproducible
dataset. Published revisions are immutable; a correction is a new revision with a changelog entry
below (SPEC §11.7 rule 2).

The generated corpus itself is not committed (SPEC §11.7): the digest below is the SHA-256 of the
`corpus-manifest.json` that `python -m corpus.generator --set v1` produces deterministically, and
`python -m corpus.check_versions --corpus <dir>` verifies a generated corpus against this pin. The
digest covers each project's evidence bundle and its authored files — the ground truth
(`expected/outcomes.json`), the applicability profile, the domain binding, and the deviation register —
and the check re-reads those files from disk, so a change to any of them, with nothing else changed,
fails the check.

## v1 — Phase 1 subset

| Field | Value |
|---|---|
| Corpus set | v1 |
| Manifest SHA-256 | `sha256:2d254ebe568a3413410a93c1e658d8c56f8048dee3139755d68d8b4d68bf593e` |
| Projects | 30 (credit domain × six implementation styles × five variants) |
| Corpus version | 2026.09 |
| Validated catalog | eu-ai-act@2026.09 |
| Dataset licence | CC-BY-4.0 (SPEC §11.7 rule 3) |
| Hugging Face revision | not yet published — the datasets are private until the GA update (G-5); publication runs from CI with the `huggingface` environment secret (HUMAN_ACTIONS H1) |

## Changelog

- **v1 (2026.09)** — initial Phase 1 subset: the credit decisioning domain, base catalog families REC,
  OVS, INT, INC.
- **v1 (2026.09), pin extended** — the corpus was never published, so the pin was corrected in place
  rather than versioned. The manifest digest now covers the ground truth, applicability profile, domain
  binding, and deviation register of every project, not only the evidence bundles. Every generated
  applicability profile now validates against the applicability-profile schema (each evidence source
  carries `class_justification`), and the coverage-gap projects declare their independent denominator by
  a schema-defined kind whose own events are the yardstick.
- **v1 (2026.09), pin re-extended (18.31)** — the corpus was still unpublished, so the pin was again
  corrected in place. `corpus/generator/generate.py`'s new `_manifest_sources()` helper declares each
  source's real trust class in every project's manifest instead of leaving it to the event's own
  self-assertion (SPEC §6.4: an undeclared source's event now defaults to `self_report`), which changes
  every generated manifest's bytes.
- **v1 (2026.09), pin re-extended (18.37a)** — the corpus was still unpublished, so the pin was again
  corrected in place. DOC-01 now compares a subject's declared components (its `BundleLoaded`
  manifests) with the tools and models its calls use, so `generate.py`'s new `_component_manifests()`
  helper gives every subject that calls tools one self-reported `BundleLoaded` declaring the MCP servers
  (or, for a call that names no server, the tool) those calls name, and the subject's applicability
  profile lists the same components under `third_party_components`, so the manifest is not read as
  applicability drift. `corpus/quickstart` is the credit/langgraph/known-pass project and carries the
  same event and profile entry.
- **v1 (2026.09), pin re-extended (18.132)** — the corpus was still unpublished, so the pin was again
  corrected in place. A verdict is now capped by its records' integrity (SPEC §6.6, §8.1), so
  `generate.py`'s `_chain()` writes each enforcement-point source's stream as `export_anchored` with a
  manifest anchor at its last record's time, and every other stream stays `export_chained`. The
  tampered-record projects' ground truth now reads `insufficient_evidence` for the controls resting on
  the broken gateway stream (OVS-03, INT-01).
- **v1 (2026.09), pin re-extended (18.134)** — the corpus was still unpublished, so the pin was again
  corrected in place. Unknown coverage now reads every INT and REC check of the subject
  `insufficient_evidence` (SPEC §6.5), so `generate.py`'s new `_coverage_export()` gives every subject an
  independent `system_of_record_export` denominator whose counts file `reference/coverage-counts[-<role>].json`
  counts the subject's accepted events per type and is listed with its sha256 in the bundle manifest. The
  coverage-gap project keeps its own denominator beside it, so it still reads a gap. Every authored ground
  truth holds unchanged; `corpus/quickstart` carries the same denominator.
- **v1 (2026.09), pin re-extended (18.37f)** — the corpus was still unpublished, so the pin was again
  corrected in place. DOC-02 now reads whether a decision records what it chose (SPEC §6.2.3 `chosen`,
  one of its `options` when it lists them), so `generate.py`'s `DECISION_CHOICE` gives every generated
  Decision two options and the one it chose. `corpus/quickstart` is the credit/langgraph/known-pass
  project and carries the same decision.
