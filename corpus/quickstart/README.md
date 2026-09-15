# Quickstart project

A small, complete credit-decisioning project an adopter can assess before instrumenting anything.
It is the input to `agentce quickstart`, which runs a full assessment over it and writes a report.

```
uv run --project engines/python agentce quickstart --out ./out
```

The bundle records one consequential credit decision with full enforcement-point evidence — a verified
delegation ending at a human overseer, the gateway-recorded tool call, and an incident register entry —
so every base-catalog control (REC, OVS, INT, INC) is conformant. See [`../../docs/quickstart.md`](../../docs/quickstart.md).
