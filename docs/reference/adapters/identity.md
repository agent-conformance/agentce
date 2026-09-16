# `identity` adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters — the `identity` adapter is marked v1.1; `§12.3` fixtures and support matrix) for identity-provider evidence.

An identity provider issues and exchanges the tokens an agent's delegation chain is built from. Its records sit at an enforcement point (the token is not minted without it), so they are trust class **`enforcement_point`** (SPEC §6.4). The adapter maps a normalised identity log (JSON Lines, one issuance or exchange per line, each naming its `format`) to **`DelegationIssued`** events:

See the [adapter README](../../../adapters/identity/README.md) for the full source-to-event mapping, fixtures, and the support matrix.
