# mcp-gateway adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3`
fixtures and support matrix) for MCP gateway request/response logs.

An MCP gateway sits in front of an agent's tool and resource calls, enforces policy, and (when it
performs token exchange) issues delegations. Because it *could have prevented* the action, its
records are trust class **`enforcement_point`** (SPEC §6.4) — so, unlike an agent's self-report (see
the `otel-genai` adapter), it can supply the **authorization link** (`refs.authorization`) that ties a
tool call to the policy decision that allowed it, and the gateway-classified effect (`side_effect`,
`effect_class`).

The adapter is a pure function `bytes → AdaptResult` over a gateway log in **JSON Lines**, one gateway
interaction per line:

| Log entry | AgentCE event(s) |
|---|---|
| `request.method` = `tools/call` (and not denied) | `ToolCall` |
| `request.method` = `resources/read` (and not denied) | `ResourceAccess` |
| a `policy` block (the gateway enforced) | `PolicyDecision` |
| a `delegation` block (token exchange) | `DelegationIssued` |

A single entry can produce several events: an enforced, allowed `tools/call` yields a `PolicyDecision`
**and** a `ToolCall` whose `refs.authorization` points at that decision (and the decision's
`refs.request` points back at the call). A denied call yields the `PolicyDecision` only — the gateway
blocked the tool, so no `ToolCall` is emitted.

## Source manifest for coverage (SPEC §6.5, §6.6)

Alongside the events, `adapt` returns a **source manifest** — the per-type record counts the gateway
observed:

```json
{"adapter": "mcp-gateway", "subject": "…", "sources": ["urn:mcp-gateway:…"], "counts": {"ToolCall": 2, "PolicyDecision": 3}}
```

The engine uses this as an **independent coverage denominator** for the agent's own self-reported
calls: an enforcement point in front of the agent is independent of the agent runtime, so its counts
reveal calls the agent failed to capture.

## Contract (SPEC §12.1)

Deterministic ids from the trace and span ids (`mcp:<traceId>/<spanId>`, with `#policy` and
`#delegation` for the decision and delegation an entry brackets); preserved timestamps; the declared
trust class on every event (never inferred); `agentceconv` = `mcp:<protocol version>`; unmapped or
malformed entries recorded in the `AdapterReport` (`invalid_json`, `not_an_object`, `missing_envelope`,
`unmapped_entry`), never emitted; tool arguments and results referenced by opaque locator rather than
captured (SPEC R12). W3C Trace Context is carried on the envelope. Standard library plus PyYAML — no
network, no learned component.

## Log entry shape

Each line is one gateway interaction. Envelope fields `timestamp`, `gateway`, `trace_id`, `span_id`
are required; an entry missing any of them is skipped. Optional: `protocol_version`, `parent_span_id`,
`task_id`, `session_id`, `agent{id,name}`, `acted_for[]`, `server`, `request{method,params}`,
`response{outcome,error,result_ref}`, `effect{side_effect,effect_class}`,
`policy{engine,policy_id,policy_version,decision,reasons,obligations,principal}`, and
`delegation{token_ref,issuer,subject_principal,actor_principal,chain,scope_granted,scope_parent,expires,verification}`.

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
```

`pytest` proves each fixture maps to its expected events and source manifest, that every event is
schema-valid, and that the contract properties above hold. `check_support_matrix` proves
`support-matrix.yaml` stays consistent with what the adapter emits over the fixtures.
