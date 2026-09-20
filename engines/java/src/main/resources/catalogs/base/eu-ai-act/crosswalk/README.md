# Crosswalks

Implements the specification: §6.9 (obligation traceability), §7.3 (catalog structure and crosswalk
hygiene), and §2.1 (the regulatory floor and the harmonised standards under request M/613).

Each file maps a framework's clause or requirement identifiers to AgentCE schema elements, correctness-
ladder rungs, and base-catalog control ids. These are traceability matrices for obligation coverage
(HR-4); the engine does not load them at assessment time.

## Files

| File | Framework | Status |
|---|---|---|
| `eu-ai-act.yaml` | EU AI Act (primary regulatory floor) | active |
| `iso-42001.yaml` | ISO/IEC 42001:2023 AI management system | active |
| `nist-ai-rmf.yaml` | NIST AI Risk Management Framework 1.0 | active |
| `owasp-asi-2026.yaml` | OWASP Agentic Security Initiative threat taxonomy | active |
| `aiuc-1.yaml` | AIUC-1 AI agent certification standard | active |
| `pren-18229-1.yaml` | prEN 18229-1 (logging, transparency, oversight) | placeholder (draft) |
| `iso-iec-24970.yaml` | ISO/IEC 24970 (AI system logging) | placeholder (draft) |

## Hygiene (SPEC §7.3)

- **Clause identifiers only.** The standards' text is never reproduced; the `obligation` field is
  AgentCE's own statement of what its evidence can show, not a quotation.
- **Every reference is unverified until a human confirms it.** Each entry carries
  `verified_against_text: false` until a reviewer with access to the source confirms the clause
  mapping. Unverified references are rendered in the report as "(clause reference unverified)". This
  human verification is tracked as a maintainer action; nothing here asserts a mapping is verified.
- **Placeholders reserve, they do not claim.** The two harmonised standards drafted under M/613 are
  not yet published, so their files carry `status: placeholder` and reserve the mapping against the
  anticipated scope; the clause-level mapping is authored when each standard publishes.

`conformance/standards_check.py` validates every file's structure, checks that each `schema_elements`
entry names a real evidence event type and each `controls` entry names a base-catalog control, and
confirms every reference carries the unverified flag.
