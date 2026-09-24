# Simulated corpus

Implements **SPEC §11** (simulated corpus specification). The corpus is a set of synthetic agent
deployments with known ground truth about which controls pass, fail, or return insufficient evidence.
It is the executable definition of correct engine behaviour and the yardstick every engine is measured
against (SPEC §11.1).

## Layout

- [`generator/`](generator/) — the deterministic, seeded generator (SPEC §11.2–11.4). It is the only
  part committed to the repository; the generated bundles are produced on demand and, for the full
  corpus, hosted as versioned datasets (SPEC §11.7).
- [`assess_one.py`](assess_one.py) — runs one generated project end to end through the engine (SPEC
  §11.5). It owns the project layout: bundle, applicability profile, domain binding, and base catalog.
- `projects/`, `golden/` — where generated bundles and reference outputs land when written locally;
  never committed (SPEC §11.7).

## Phase-1 subset

Item 1.10 generates the Phase-1 subset: the **credit decisioning** domain crossed with six
implementation styles and five variants — thirty projects — exercising the base catalog families REC,
OVS, INT, INC (SPEC §7.3). Generate and assess:

```
uv run --project corpus python -m corpus.generator --out out/corpus
uv run --project corpus python -m corpus.assess_one --corpus out/corpus --project credit/langgraph/known-pass --out out/report
```

The generator is deterministic (fixed seed, no clock, no network); the same set produces byte-identical
output on any machine (HR-1). Each project carries its authored ground truth in `expected/outcomes.json`
and a narrative `README.md`; the corpus test suite proves those outcomes against the reference engine,
so the precision/recall gate (SPEC §11.6) sees recall 1.0 on seeded faults and a false-positive rate of
0.0 on known-pass controls.

## Full corpus

`--set full` emits the full corpus (SPEC §11.2): three domains crossed with the implementation styles
and seven variants (minus the recipes reserved for the held-out and adversarial subsets), plus
multi-agent, held-out, and adversarial projects — roughly 130 projects in all:

```
uv run --project corpus python -m corpus.generator --set full --out out/corpus-full
```

Every project in the full set carries the same authored ground truth, applicability profile, domain
binding, and deviation register as the Phase-1 subset, and is covered by the same determinism guarantee.
