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
./verification/run --demo-fault VG-SUITE-SELFTEST --fault 1   # only the gate's second seeded fault
./verification/run --lint-registry
./verification/run --quick --json           # machine-readable results (see results.schema.json)
```

It needs Python 3 for the runner itself, plus whatever a gate lists under `requires` (for example `uv`). A gate
whose tool is missing is reported as `skipped` with the reason, never as `pass`. Exit codes: 0 no gate failed,
1 a gate failed or the registry was rejected, 2 the command line was refused: a gate or fault that does not exist,
two commands at once, an option given twice (only `--fault` repeats), or an option the command does not use
(`--fault` without `--demo-fault`, `--json` with `--list` or `--lint-registry`, `--demo-reject` without
`--lint-registry`). Each refusal names its message key.

## What a gate is

Each gate is an entry in [`gates.json`](gates.json) with:

- a stable id (`VG-…`) and a descriptive title;
- a tier: `quick` (offline, minutes, any machine) or `full` (run in CI);
- the command that decides it, its working directory, its timeout, and the tools it needs;
- a **rubric**: what makes it pass and what makes it fail;
- a **claim** in the public claims register, or an **invariant** stated in `AGENTS.md`, that it backs;
- at least one **seeded fault**: a small, exact edit that must turn the gate RED.

`--quick` and `--full` tell each gate which tier it runs in through `AGENTCE_VERIFY_TIER` (`quick` or `full`).
A gate with a quick subset reads it: `VG-TRUST-MATRIX` runs the 67 cells of its matrix's quick list in a quick run
and all 782 in a full one. `--gate` passes your environment through unchanged, so
`AGENTCE_VERIFY_TIER=full ./verification/run --gate VG-TRUST-MATRIX` runs the whole matrix for that gate alone.

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

CI runs every seeded fault of every gate across the `demo-fault` matrix. The unit of work is one (gate, fault) pair.
`verification/shard.py` reads the seconds recorded per gate in [`demo-durations.json`](demo-durations.json) and
splits them evenly over the gate's fault runs and its one restore run. A gate whose seconds are over
`max_chunk_seconds` (240) is cut into runs of consecutive faults, each run costing its faults plus one restore. These
chunks go to the shards longest first, each to the shard with the least work so far, and a shard runs
`./verification/run --demo-fault <ID> --fault=<n> ...` once per chunk. A gate with no recorded seconds counts as
`default_seconds` (300, about twice the mean demo), so a new gate is treated as heavy until it is timed. To see the
estimate for a lane count, or the pairs one shard runs:

```
python3 verification/shard.py --balance-check 26
python3 verification/shard.py --dry-run 0 26      # one "<gate> <fault>" line per pair
```

The balance check prints each shard's estimated minutes. It exits 1 when a gate has no recorded seconds and no
`default_seconds` is set, when the heaviest shard is over the larger of 1.25 times the mean and the longest chunk,
or when any shard is over `lane_bound_minutes` (12.0). VG-DEMO-SHARD-COVERAGE runs the same check at the workflow's
lane count.

The estimates are the longest time each gate took in the CI runs named under `source` and leave out each job's setup (a minute or two).
The same gate can take half or twice as long from one run to the next, depending on the machine a job gets, so
read them as rough. To refresh them, take one or more green `verification` runs whose demo-fault jobs all
finished, then:

```
gh run view <run-id> --log > run.log
```

For each demo-fault job, a chunk's seconds are the gap between its `RED on the seeded fault` line and the previous
one in the same job (the first chunk from the start of the job's "This shard's seeded-fault demos" step). A gate cut
into k chunks over n faults ran k restores instead of one, so its seconds are the sum of its chunks' times multiplied
by (n + 1) / (n + k). Write the longest over the runs under `gates` and list each run, attempt and commit under
`source`.

## Adding a gate

A new feature, adapter or catalog adds or extends a gate in the same change: an entry in `gates.json` with a
seeded fault and a claim or invariant, a line in `CHANGELOG.md`, and `./verification/run --quick` green.
