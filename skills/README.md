# AgentCE agent skills

The approved skill set (SPEC §13.3). Skills sit on the two edges of the engine — inputs and outputs —
and are **never in the evaluation path** (§13.3.1). The engine's guarantees (HR-1/HR-2, no learned
component) are its own; these rules keep a skill from compromising them from either side.

| Skill | Purpose | Status |
|---|---|---|
| [`agentce-onboard`](agentce-onboard/) | Declare, instrument, and iterate a codebase until its bundle validates with the minimum evidence for the target controls. | built |
| `agentce-check-report` | Pre-run checks, manual checklists, deviations, and reading outcomes on the report side. | later phase |
| [`evals/`](evals/) | Skill evaluation tasks and recorded results (§13.3.5). | scaffold |

## Trust rules (every skill obeys these; SPEC §13.3.2)

- **No fabricated evidence** (S-1); **honest trust class** (S-2); **protected artifacts read-only** (S-3).
- **Deterministic, offline, model-free scripts** (S-4): the model reasons, the scripts check.
- **Version pinning** (S-5): each script verifies the SKILL.md spec/CLI/catalog pins against the engine
  and stops on mismatch (exit 3) rather than proceeding with stale field names.
- **Behaviour preservation** (S-6), **provenance in a skill log** (S-7), **human confirmation at legal
  or policy decisions** (S-8), **content minimisation** (S-9), **signed, pinned distribution** (S-10).

## Script conventions

Every script accepts `--json`, takes `--repo`/`--bundle`/`--profile` as applicable, prints a
human-readable line (or table) plus JSON, and returns exit code `0` (ok), `1` (findings that require
action), `2` (input error), or `3` (version mismatch).

## Install and pin (S-10)

Skills are published from this monorepo at tagged releases with Sigstore signatures and installed by
**commit-pinned** reference; registries receive pinned references only. Pin to a release tag or commit,
never to a moving branch. Each skill's `SKILL.md` frontmatter pins the spec, CLI, and catalog versions
it was written against; a mismatch stops the skill.

Each skill folder is **one-command installable on its own**, severed from this monorepo: copy the
folder anywhere (a zip, an assistant's skill directory, a registry checkout) and run its self-test
directly, with no sibling `engines/` checkout beside it —

```sh
uv run --frozen python3 scripts/lint_profile.py --self-test --json   # agentce-onboard
```

This works because each skill vendors a real wheel of `engines/python` under its own `vendor/`
directory (`pyproject.toml`'s `[tool.uv.sources]` resolves `agent-conformance` from that local file,
never a relative path back into this repository). `tools/vendor_skill_engine.py` keeps every skill's
vendored wheel in sync with `engines/python`; run it with `--write` after a change to the engine and
commit the rebuilt `vendor/*.whl` alongside the skill's re-locked `uv.lock`.

## Version table

| Skill | skill_version | spec_version | cli_version | catalog_versions |
|---|---|---|---|---|
| `agentce-onboard` | 1.0.0 | 0.6 | >=0.1.0,<0.2 | eu-ai-act@2026.09 |
