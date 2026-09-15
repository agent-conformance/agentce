# oversight adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3`
fixtures and support matrix; trust class per `§6.4`) for human-oversight records.

Human oversight is evidenced by systems outside the agent runtime — approval gates, workflow engines,
and ticketing systems. When the records are kept by a system in which no principal in the agent's
delegation chain can transition approval states, they are trust class **`independent_system`**;
otherwise (an in-process approval prompt, an agent-written log) they are **`self_report`**. The
adapter maps a normalised oversight log (JSON Lines, one record per line, each naming its `kind` and
`source_format`):

| Record `kind` | AgentCE event |
|---|---|
| `approval_requested` | `ApprovalRequested` |
| `approval_decided` | `ApprovalDecided` |
| `override` | `Override` |
| `interrupt` | `Interrupt` |
| `notice` | `Notice` |

`source_format` is one of `approval_gate`, `workflow_engine`, `ticketing`, and becomes the recorded
convention (`agentceconv`).

## Class-justification enforcement (SPEC §12.2, §6.4)

The declared trust class is passed by the caller. A claim of `independent_system` **must** be
justified — `adapt(..., source_class="independent_system")` without a `class_justification` is refused
(`class_justification_required`). At ingest, the engine's `class_mismatch` check further guards that an
event's class matches the class the bundle declares for its source.

A decision, override, or interrupt must name its **human actor** with a `role` and an
`authority_ref`, and a decision also carries the identity-provider `session_ref` (SPEC §12.2); a
record that does not is reported (`incomplete_record`), never emitted.

## Contract (SPEC §12.1)

Deterministic ids (`oversight:<record id>`); preserved timestamps; the declared trust class on every
event; malformed or partial records recorded in the `AdapterReport` (`invalid_json`, `not_an_object`,
`missing_envelope`, `unknown_kind`, `incomplete_record`), never emitted. W3C Trace Context is carried
on the envelope when present. Standard library plus PyYAML — no network, no learned component.

An oversight event records the human action but not the agent decision it concerns; that reference
(`refs.decision`) needs the agent-side decision stream to correlate, so the support matrix lists it
under `missing`.

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
```

`pytest` proves each fixture maps to its expected events byte-for-byte, that every event is
schema-valid, and that the contract properties above hold. `check_support_matrix` proves
`support-matrix.yaml` stays consistent with what the adapter emits over the fixtures.
