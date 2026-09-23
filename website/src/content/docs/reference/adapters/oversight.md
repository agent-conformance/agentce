---
title: oversight adapter
description: Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix; trust class per `§6.4`) f
---

Implements the adapter contract of **SPEC §12** (`§12.1` contract, `§12.2` v1 adapters, `§12.3` fixtures and support matrix; trust class per `§6.4`) for human-oversight records.

Human oversight is evidenced by systems outside the agent runtime — approval gates, workflow engines, and ticketing systems. When the records are kept by a system in which no principal in the agent's delegation chain can transition approval states, they are trust class **`independent_system`**; otherwise (an in-process approval prompt, an agent-written log) they are **`self_report`**. The adapter maps a normalised oversight log (JSON Lines, one record per line, each naming its `kind` and `source_format`):

See the [adapter README](https://github.com/agent-conformance/agentce/blob/main/adapters/oversight/README.md) for the full source-to-event mapping, fixtures, and the support matrix.
