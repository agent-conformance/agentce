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
| Manifest SHA-256 | `sha256:525b3f503ab7f0fcb75e2d99b8732e54bee756da25d7bdad46e29aeb6ab691f0` |
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
