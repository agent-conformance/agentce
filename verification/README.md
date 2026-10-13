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

## Demo shard durations

CI runs every gate's seeded-fault demo across the `demo-fault` matrix. `verification/shard.py` gives the gates to
the shards longest first, each to the shard with the least work so far, using the seconds recorded per gate in
[`demo-durations.json`](demo-durations.json). A gate with no recorded seconds counts as `default_seconds` (300,
about twice the mean demo), so a new gate is treated as heavy until it is timed. To see the estimate for a lane
count:

```
python3 verification/shard.py --balance-check 18
```

It prints each shard's estimated minutes and exits 1 when a gate has no recorded seconds and no `default_seconds`
is set, or when the heaviest shard is over the larger of 1.25 times the mean and the longest single gate.
VG-DEMO-SHARD-COVERAGE runs the same check at the workflow's lane count.

The estimates come from one CI run and leave out each job's setup (a minute or two). To refresh them, take a green
`verification` run whose demo-fault jobs all finished, then:

```
gh run view <run-id> --log > run.log
```

For each demo-fault job, a gate's seconds are the gap between its `RED on the seeded fault` line and the previous
one in the same job (the first gate from the start of the job's "This shard's seeded-fault demos" step). Write them
under `gates` and set `source` to that run, attempt and commit.

## Adding a gate

A new feature, adapter or catalog adds or extends a gate in the same change: an entry in `gates.json` with a
seeded fault and a claim or invariant, a line in `CHANGELOG.md`, and `./verification/run --quick` green.
