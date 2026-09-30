# AgentCE agent skills

The approved skill set (SPEC §13.3). Skills sit on the two edges of the engine — inputs and outputs —
and are **never in the evaluation path** (§13.3.1). The engine's guarantees (HR-1/HR-2, no learned
component) are its own; these rules keep a skill from compromising them from either side.

| Skill | Purpose | Status |
|---|---|---|
| [`agentce-get-evidence`](agentce-get-evidence/) | Declare, instrument, and iterate a codebase until its bundle validates with the minimum evidence for the target controls. | built |
| [`agentce-prepare-to-share`](agentce-prepare-to-share/) | Pre-run checks, manual checklists, deviations, and reading outcomes on the report side. | built |
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
uv run --frozen python3 scripts/lint_profile.py --self-test --json   # agentce-get-evidence
uv run --frozen pytest -q                                            # agentce-prepare-to-share
```

This works because each skill vendors a real wheel of `engines/python` under its own `vendor/`
directory (`pyproject.toml`'s `[tool.uv.sources]` resolves `agent-conformance` from that local file,
never a relative path back into this repository). `tools/vendor_skill_engine.py` keeps every skill's
vendored wheel in sync with `engines/python`; run it with `--write` to rebuild the wheel and re-lock
each skill.

**`vendor/*.whl` and `uv.lock` are not committed on everyday commits**: the wheel changes on every engine
change, and committing it every time made each one another CI run for no reason. `.gitignore` excludes
both paths; CI runs `tools/vendor_skill_engine.py --write` itself before testing either skill, so the
standalone self-tests above still exercise a real, freshly built wheel on every run. The sync test still
proves each skill bundles the engine it was tested with, just without a commit recording it.

**A release commit is different, and it is cut off-branch.** As the last step of cutting a release, in a
throwaway `git worktree` detached from `HEAD` -- never on `main` or a phase branch, so the wheel and lock
never re-enter that branch's tracked history and every later phase commit on the branch stays clean under
"`vendor/*.whl` and `uv.lock` are not committed" above -- run `vendor_skill_engine.py --release`, tag the
worktree's commit, push the tag, then remove the worktree:

```sh
git worktree add --detach /tmp/agentce-release HEAD
cd /tmp/agentce-release
tools/vendor_skill_engine.py --release
git tag vX.Y.Z
git push origin vX.Y.Z
cd -
git worktree remove --force /tmp/agentce-release
```

`--release` is the one piece of code that cuts a release commit: it runs `--write`, refuses to commit if
that leaves any tracked file other than the gitignored wheel and lock dirty (`--write` must find each
skill's `pyproject.toml` already in sync with the engine version being released -- it is, if every engine
change was committed through the normal phase-commit process; if it isn't, `--write` leaves
`pyproject.toml` modified on disk and `--release` refuses, so a stale, uncommitted source line can never
ship inside the release commit next to an in-sync wheel -- commit that `pyproject.toml` fix as its own
normal phase commit first, then cut the release again), then force-adds and commits `uv.lock` and
`vendor/*.whl` for both skills by name (`.gitignore` makes a plain `git add -A` silently skip them).

That release commit carries `vendor/*.whl` and `uv.lock` for both skills, so a commit-pinned checkout of
the tag it carries keeps working exactly as documented above: one command, standalone, offline.
`tools/skill_release_shape_check.py` proves it, running this exact `--release` command rather than a
second copy of its logic: on an ordinary phase commit it cuts one in a throwaway worktree and validates
the result; on a commit that already is a release commit (the ordinary case once a release has been cut)
it validates that commit directly instead of trying to cut a second one on top. Either way it extracts
each skill folder from the committed tree alone and runs both self-test commands above from it with
networking disabled; it also rebuilds each wheel fresh from that commit's own `engines/python` and
confirms it matches the committed one, byte for byte, so a release commit vendored before a later,
uncommitted engine change would be caught even though its wheel still installs and runs. Its `--self-test`
proves a release commit with a missing, corrupted or stale wheel turns that check red.

## Version table

| Skill | skill_version | spec_version | cli_version | catalog_versions |
|---|---|---|---|---|
| `agentce-get-evidence` | 1.0.0 | 0.6 | >=0.1.0,<0.2 | eu-ai-act@2026.09 |
