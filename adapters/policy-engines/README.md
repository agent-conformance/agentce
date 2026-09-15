# policy-engines adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3`
fixtures and support matrix) for authorization decision logs.

Policy engines sit at an enforcement point and decide whether an action is allowed, so their records
are trust class **`enforcement_point`** (SPEC §6.4). This adapter has **one sub-adapter per engine**,
all producing the same canonical shape:

| Engine | Native log | AgentCE event |
|---|---|---|
| **OpenFGA** | relationship-check logs | `AuthzCheck` (user / relation / object / allowed / store) |
| **OPA** | decision logs | `PolicyDecision` |
| **Cedar** | authorization logs | `PolicyDecision` (a thin sub-adapter, exercised by fixtures) |
| **governance-toolkit** | audit logs | `PolicyDecision` |

The engine is chosen by the caller — `adapt(payload, engine="opa", subject=…)` — because the
collector knows which engine's logs it is reading. Each engine's raw decision spelling (`true`/`false`,
`Allow`/`Deny`, `permit`/`deny`, …) is normalised to the canonical `PolicyDecisionOutcome`
(`allow`/`deny`/`require_approval`/`transform`).

## Input

JSON Lines, one decision per line, wrapped in a small envelope. Required: `timestamp` and a stable id
(`id`, `decision_id`, `check_id`, or `request_id`; failing that, `trace_id` + `span_id`). Optional
envelope: `convention_version`, `instance`, `trace_id`/`span_id`/`parent_span_id`, `task_id`,
`session_id`, `agent{id,name}`, `acted_for[]`. Engine-specific: OpenFGA reads
`tuple_key{user,relation,object}`, `allowed`, `store_id`; the decision engines read `path`/`policy_id`,
`result`/`decision`, `revision`/`policy_version`, `policy_digest`, `reasons`, `obligations`,
`principal{id,kind,role}`.

## Contract (SPEC §12.1)

Deterministic ids (`<engine>:<decision id>`); preserved timestamps; the declared trust class on every
event (never inferred); `agentceconv` = `<engine>:<version>`; entries that are malformed or lack the
fields an event needs are recorded in the `AdapterReport` (`invalid_json`, `not_an_object`,
`missing_envelope`, `incomplete_decision`), never emitted. W3C Trace Context is carried on the
envelope when present. Standard library plus PyYAML — no network, no learned component.

A policy engine's log records the decision but not the action it authorised; that reference
(`refs.request`) needs an enforcement-point call log, so the support matrix lists it under `missing`
(the `mcp-gateway` adapter supplies it).

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
```

`pytest` proves each fixture maps to its expected events byte-for-byte, that every event is
schema-valid, and that the contract properties above hold. `check_support_matrix` proves
`support-matrix.yaml` stays consistent with what the adapter emits over the fixtures.
