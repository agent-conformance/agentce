# The verification suite

Every change to this repository has to pass fixed **build gates**. This folder holds them, and one command runs
them. Think of it as an eval harness for the codebase: fixed gates, one runner, reproducible results you can compare against ours.

It implements the engineering rules in [`AGENTS.md`](../AGENTS.md) (Quality gates). It is separate from the
conformance suite in [`conformance/`](../conformance/), which tests any engine against the specification, and
from `agentce verify`, which checks a report's signature and integrity.

## Run it

```
./verification/run --quick                  # every quick-tier gate; offline
./verification/run --full                   # every gate
./verification/run --gate VG-SUITE-SELFTEST # one gate
./verification/run --demo-fault VG-SUITE-SELFTEST
./verification/run --lint-registry
./verification/run --quick --json           # machine-readable results (see results.schema.json)
```

It needs Python 3 for the runner itself, plus whatever a gate lists under `requires` (for example `uv`). A gate
whose tool is missing is reported as `skipped` with the reason, never as `pass`. Exit codes: 0 no gate failed,
1 a gate failed or the registry was rejected, 2 the command named a gate that does not exist.

## What a gate is

Each gate is an entry in [`gates.json`](gates.json) with:

- a stable id (`VG-…`) and a descriptive title;
- a tier: `quick` (offline, minutes, any machine) or `full` (run in CI);
- the command that decides it, its working directory, its timeout, and the tools it needs;
- a **rubric**: what makes it pass and what makes it fail;
- a **claim** in the public claims register, or an **invariant** stated in `AGENTS.md`, that it backs;
- at least one **seeded fault**: a small, exact edit that must turn the gate RED.

`./verification/run --demo-fault <ID>` applies each seeded fault, shows the gate RED, restores the file byte for
byte, and shows it GREEN. If a demo is interrupted, `git checkout -- <file>` restores the file it names.

`./verification/run --lint-registry` rejects a gate that lacks a seeded fault, a claim or invariant, a rubric, a
well-formed id, a fault that applies, or an entry in [`CHANGELOG.md`](CHANGELOG.md).
`--lint-registry --demo-reject` shows it rejecting deliberately bad registries.

## Reading a result

A failing gate prints the tail of its output, a `fix:` hint and the `re-run:` command; `--json` carries the same
in `output_tail`, `fix_hint` and `rerun`.

## What it does not show

A green run says the gates passed; it does not say the code has no other faults. The full tier and the release
results are added as the suite grows, and every change to a gate is recorded in the changelog.

## Adding a gate

A new feature, adapter or catalog adds or extends a gate in the same change: an entry in `gates.json` with a
seeded fault and a claim or invariant, a line in `CHANGELOG.md`, and `./verification/run --quick` green.
