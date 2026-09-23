---
title: mcp-gateway adapter
description: Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix) for MCP gateway request/r
---

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix) for MCP gateway request/response logs.

An MCP gateway sits in front of an agent's tool and resource calls, enforces policy, and (when it performs token exchange) issues delegations. Because it *could have prevented* the action, its records are trust class **`enforcement_point`** (SPEC §6.4) — so, unlike an agent's self-report (see the `otel-genai` adapter), it can supply the **authorization link** (`refs.authorization`) that ties a tool call to the policy decision that allowed it, and the gateway-classified effect (`side_effect`, `effect_class`).

See the [adapter README](https://github.com/agent-conformance/agentce/blob/main/adapters/mcp-gateway/README.md) for the full source-to-event mapping, fixtures, and the support matrix.
