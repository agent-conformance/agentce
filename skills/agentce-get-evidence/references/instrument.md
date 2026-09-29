# Phase B — Instrument

The maintained home of the SPEC §13.1 universal instrumentation prompt, applied to the codebase on a
feature branch. This skill adds capture, correlation, and declarations only; it never changes what the
agent decides or does (S-6), and it never emits an event for something the code did not observe (S-1).

1. **Inventory.** Run `find_chokepoints --repo . --json` and complete it by reading any low-confidence
   sites. The scanner reports, per path and line: model calls; tool/resource calls; authorization;
   credential acquisition; approval/human-input; memory and retrieval; startup component loads;
   consequential decisions; incident and disclosure paths; and, for the Conduct overlay,
   instruction-entry and refusal paths. Each entry carries `kind`, `confidence`, and
   `enforcement_point_present`.
2. **Prefer enforcement-point evidence** (SPEC §13.1 STEP 2). For every tool, authorization, and
   credential chokepoint with an enforcement point available, route through it and record the
   enforcement point's request identifier in the agent's span. Do not add agent-side emission where an
   enforcement point exists.
3. **Trace context.** Enable the framework's OpenTelemetry GenAI instrumentation (see
   `references/frameworks/<style>.md`) so `invoke_agent`, `chat`, and `execute_tool` spans exist at the
   pinned convention version; propagate W3C Trace Context across every model call, tool call,
   sub-agent call, and asynchronous continuation; carry the task id as `agentcetask`.
4. **Emit.** Add the emitter (`agentce-emit` for the language) and emit the §13.1 events at the
   inventoried chokepoints, typed with the binding's IRIs. Hash content by default (S-9); derive ids
   deterministically; use source timestamps; label `self_report` for all agent-side emission (S-2).
5. **Integrity.** Configure hash-chaining per stream and signing where available; emit stream heads
   into the bundle manifest.
6. **Bundle.** Produce a representative bundle from a test run or a short production window into
   `agentce/bundles/<date>/`.
7. **Gaps.** Record in `docs/agentce/gaps.md` every chokepoint with no hook (S-1: never simulate the
   event) and every place where capture would change behaviour (S-6: stop and report).
8. **Conduct overlay (SPEC §7.7.5).** Emit `Instruction` at every point where an instruction enters the
   agent, with its `source_class`, principal, hashed content, and `refs.parent`/`refs.origin`; set
   `refs.instruction` on every `ToolCall` and consequential `Decision`; set `effect_class` at tool
   dispatch; emit `Refusal` on every refusal path with its reason class and the enforcement point's
   `PolicyDecision` reference when one exists.
