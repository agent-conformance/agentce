# Phase C — Validate and iterate

The procedure for closing the loop until the bundle carries the minimum evidence for the target
controls (SPEC §13.3.3 Phase C). Every step is read-only over the engine's outputs (S-3).

1. **Preflight.** `bundle_preflight --bundle <path> --profile agentce/applicability.yaml --json`:
   schema quarantines by reason; per-source event counts; trust-class consistency between each event's
   `agentcesourceclass` and the profile's declared class; and dangling references.
2. **Assess.** Run `agentce assess` with the target catalog against the bundle. Do not touch the
   outputs — `assertions.json`, the report, and any signed claim are protected artifacts.
3. **Explain.** `explain_insufficient --assertions <report>/assertions.json --support-matrices
   <adapters> --json`: for every `insufficient_evidence` outcome it prints the control, the missing
   event member, the minimum source class required versus the best available, and the adapter that
   could supply the missing member — grouped by root cause so collection is fixed once, not control by
   control.
4. **Iterate.** Apply techniques in order of coverage gained per change, re-emit, and re-run preflight
   and assess, until every applicable target control has its minimum evidence and coverage is above the
   catalog threshold for each enforcement-point source, or the remaining gaps are documented in
   `docs/agentce/gaps.md` as organisational (no enforcement point exists; a source cannot export; a
   policy is undeclared).
5. **Definition of done** (printed by the skill and included in the PR): zero schema quarantines; every
   declared source present in the bundle; trust classes consistent; minimum evidence met for all
   applicable target controls or documented as organisational gaps; the project test suite unchanged;
   `lint_profile` clean; the skill log complete.

**Failure modes to handle explicitly.** No enforcement point for tools — proceed with `self_report`
and say so prominently. Mixed convention versions across services — record `agentceconv` per source
and let the adapters map. Asynchronous continuations that break trace context — document the break and
the fix. Frameworks without hooks for approvals — approvals come from the approval system's export via
the `oversight` adapter, never from the agent.
