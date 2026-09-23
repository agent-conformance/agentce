---
title: supply-chain adapter
description: Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix; trust class per `§6.4`) f
---

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix; trust class per `§6.4`) for supply-chain evidence.

Supply-chain evidence comes from admission controllers and registries — Sigstore/in-toto attestations, CycloneDX AIBOMs, registry pins, and runtime bundle-load logs — so its records are trust class **`enforcement_point`** (admission) or **`independent_system`** (registry). The adapter maps a normalised supply-chain log (JSON Lines, one record per line, each naming its `kind` and `format`):

See the [adapter README](https://github.com/agent-conformance/agentce/blob/main/adapters/supply-chain/README.md) for the full source-to-event mapping, fixtures, and the support matrix.
