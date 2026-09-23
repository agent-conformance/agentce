# 0016 — Framework examples get their own environment, exempt from the no-ml scan by name

Status: accepted
Spec refs: SPEC §8.7, §13.4 AX-3, AX-4

## Context

`examples/<style>/` (SPEC §13.4 AX-4) exists to show a real agent framework producing AgentCE
evidence. Making that real means each of `langgraph`, `openai-agents`, `crewai`, `google-adk`, and
`claude-agent-sdk` actually imports and runs its named framework — offline, keyless, against a
scripted deterministic model — instead of a framework-free stand-in.

`tools/no_ml_check.py` (SPEC §8.7, HR-1/HR-2) scans every `uv.lock`, `pnpm-lock.yaml`, and
`gradle.lockfile` in the repository, unconditionally, for any package on
`spec/rules/no-ml-denylist.txt`. That is a hard invariant: no engine, adapter, or evaluation-path
dependency tree may resolve to a learned component or an LLM/embedding client, and nothing may narrow
the scan to make a violation disappear.

An agent framework's own dependency tree is not held to that invariant — it is the thing being
observed, not part of the engine that observes it — but it routinely pulls in exactly the package
families the denylist bans. Installing `langgraph` alone stays clean, but `crewai` requires
`openai`, `chromadb` (which pulls `onnxruntime`), and `tokenizers`; `google-adk`, `openai-agents`,
and `claude-agent-sdk` each require at least one of `openai`/`anthropic`/`google-generativeai` as a
hard dependency of their own SDK, even though the example's scripted model never calls out to any of
them. Left as-is, giving these examples real dependencies would fail the no-ml scan the moment their
lockfiles existed — not because the engine gained a learned-component dependency, but because the
scan does not yet distinguish "an engine tree" from "a framework example being exercised for real."

## Decision

1. **Each of the five framework examples gets its own uv project**
   (`examples/<style>/pyproject.toml` + its own `uv.lock`), declared exactly like
   `adapters/*`/`engines/*` already are: `[tool.uv.sources]` points `agentce-emit` at
   `../../engines/python-emit` (editable), and the one named framework package is a normal
   dependency. This isolates each framework's transitive tree from the shared `examples/` root
   (still used by `custom-loop`, `mcp-server`, and `a2a-mesh`, which have no framework dependency and
   stay on the no-ml scan's clean side) and from every engine/adapter/conformance/tools tree.

2. **`tools/no_ml_check.py` gains one new, narrowly-named constant**,
   `FRAMEWORK_EXAMPLE_LOCKS`, listing the five exact lockfile paths this decision creates
   (`examples/langgraph/uv.lock`, `examples/crewai/uv.lock`, `examples/openai-agents/uv.lock`,
   `examples/google-adk/uv.lock`, `examples/claude-agent-sdk/uv.lock`). A lockfile on that list is
   skipped by the scan; every other lockfile in the repository, including the shared
   `examples/uv.lock` and every `engines/*`, `adapters/*`, `conformance/*`, and `tools/*` lockfile, is
   scanned exactly as before. The list is paths, not a directory prefix or a glob over `examples/**`,
   so a sixth example added later is scanned by default and must earn its own exemption line here and
   in this ADR — the boundary cannot silently widen.

3. **The exemption is a named allowlist, never a loosened rule.** `no_ml_check.py --self-test` proves
   a denylisted package is still caught in a lockfile that is *not* on the list (including a lockfile
   under `examples/` itself, so the shared root stays covered), and that a denylisted package inside a
   `FRAMEWORK_EXAMPLE_LOCKS` path is exempt only there. `EXCLUDE_DIRS` (`.venv`, `node_modules`,
   `.gradle`) is unchanged — those remain vendored/build-cache exclusions, a different mechanism from
   this named, auditable list.

## Alternatives considered (with why not)

- **Exclude all of `examples/` from the scan.** Rejected: it would also stop scanning
  `examples/uv.lock` (the shared root used by the three frameworks that need no framework
  dependency), silently widening the exemption to any future example without a documented reason.
- **A directory-prefix or glob exemption (`examples/*/uv.lock`).** Rejected for the same reason at
  finer grain: a sixth framework example would be exempt automatically, with no new line here to
  review. An explicit path list makes every exemption a diffable, reviewable addition.
- **Vendor a stripped-down framework fork with the model-client dependency removed.** Rejected: it
  would no longer be the real framework a user actually installs, defeating the point of the example,
  and would add an ongoing fork-maintenance burden for no safety gain (the engine trees never depend
  on these packages either way).
- **A per-lockfile allowlist of denylisted package names, still scanning every lockfile.** Considered
  and rejected as more complex for no more safety: the boundary this finding cares about is *which
  trees* may carry these packages, not which specific package names within an already-out-of-scope
  tree — a framework example's dependency set is expected to change as frameworks release new
  versions, and re-approving each name would just add churn without catching anything the tree-level
  exemption does not already catch.

## Consequences (determinism, portability, performance)

- **Determinism / offline.** `no_ml_check.py` remains standard-library only, no network, no clock or
  locale dependence; the exemption is a static path comparison.
- **Portability.** This is a Python-repository-level example concern; it implies nothing for the
  TypeScript or Java engines, which have no equivalent example directories today.
- **Performance.** Negligible — one more `in` check per discovered lockfile.
- **Coverage stays honest.** Every engine, adapter, conformance, and tools lockfile — and the shared
  `examples/uv.lock` — is still scanned unconditionally. The five framework-example lockfiles are the
  only lockfiles in the repository permitted to resolve a denylisted package, and only because their
  packages are the subject of the example, never a dependency an assessment run relies on.

## Verification (the checks that prove the decision holds)

- `tools/no_ml_check.py --self-test` (extended by this change): plants a denylisted package in a
  synthetic lockfile at a path *not* in `FRAMEWORK_EXAMPLE_LOCKS` (including one under a synthetic
  `examples/` root) and asserts it is still caught; plants one at a `FRAMEWORK_EXAMPLE_LOCKS` path and
  asserts it is exempt there and only there.
- The `no-ml` CI job (`.github/workflows/no-ml.yml`) runs the same scan, unconditionally, on every
  push and pull request — unchanged by this decision except for the five named exemptions.
- Each framework example's own `pyproject.toml`/`uv.lock` is reviewed the same way any dependency
  change is (SPEC §8.7 note; AGENTS.md "Dependencies and security hygiene"): a real, aged release of
  the named framework, nothing else added without a stated reason.
