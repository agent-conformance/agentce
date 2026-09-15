# identity adapter (v1.1)

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters — the
`identity` adapter is marked v1.1; `§12.3` fixtures and support matrix) for identity-provider evidence.

An identity provider issues and exchanges the tokens an agent's delegation chain is built from. Its
records sit at an enforcement point (the token is not minted without it), so they are trust class
**`enforcement_point`** (SPEC §6.4). The adapter maps a normalised identity log (JSON Lines, one
issuance or exchange per line, each naming its `format`) to **`DelegationIssued`** events:

| Source `format` | AgentCE event |
|---|---|
| `token-exchange` (OAuth 8693 token exchange) | `DelegationIssued` |
| `idp-issuance` (OIDC issuance) | `DelegationIssued` |

Each event carries the delegation `chain` (typed principals), the `scope_granted` and `scope_parent`,
the token `expires`, and the token `verification`. The **chain-attenuation check** — that each hop's
granted scope is within its parent's — is **engine-side** (SPEC §12.2), not the adapter's; the adapter
records `scope_granted` and `scope_parent` faithfully for the engine to compare.

## Contract (SPEC §12.1)

Deterministic ids (`identity:<record id>`); preserved timestamps; the declared trust class on every
event; a recorded convention (`<format>:<version>`); a record without a `token_ref` or missing its
envelope is recorded in the `AdapterReport` (`invalid_json`, `not_an_object`, `missing_envelope`,
`incomplete_record`), never emitted. W3C Trace Context is carried on the envelope when present.
Standard library plus PyYAML — no network, no learned component.

## Commands

```bash
uv run pytest -q
uv run python -m agentce_adapters.check_support_matrix .
```

`pytest` proves each fixture maps to its expected events byte-for-byte, that every event is
schema-valid, and that the contract properties above hold. `check_support_matrix` proves
`support-matrix.yaml` stays consistent with what the adapter emits over the fixtures.
