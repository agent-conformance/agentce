# `policy-engines` adapter

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix) for authorization decision logs.

Policy engines sit at an enforcement point and decide whether an action is allowed, so their records are trust class **`enforcement_point`** (SPEC §6.4). This adapter has **one sub-adapter per engine**, all producing the same canonical shape:

See the [adapter README](../../../adapters/policy-engines/README.md) for the full source-to-event mapping, fixtures, and the support matrix.
