# The skill log

Every run of `agentce-onboard` appends one JSON object per line to `agentce/skill-log.jsonl` in the
target repository (rule S-7). The log is the skill's own provenance: what it did, against which
versions, and which judgement calls a person confirmed. It is written in the target repo at run time —
it is not shipped in this package.

Each entry records:

- `skill` and `skill_version` — from the SKILL.md frontmatter.
- `spec_version`, `cli_version`, `catalog_versions` — the pins verified against the engine (S-5).
- `phase` — `declare`, `instrument`, or `validate-iterate`.
- `action` — the step taken (e.g. `wrote agentce/applicability.yaml`, `ran find_chokepoints`).
- `files` — created or modified paths, each with a SHA-256 digest (never content, S-9).
- `commands` — the scripts and CLI commands executed, with exit codes.
- `confirmations` — decisions a named person confirmed (Annex III classification, decision typing,
  oversight, deviations), each with `confirmed_by` (S-8).
- `timestamp` — RFC 3339 UTC.

The log never contains prompt or tool content, personal data, or secrets (S-9): it records hashes,
locations, and counts. Protected artifacts — `assertions.json`, bundles, `manifest.json`, signed
claims, the deviation register, and manual records — are never written by the skill (S-3); the log
records only that they were read.
