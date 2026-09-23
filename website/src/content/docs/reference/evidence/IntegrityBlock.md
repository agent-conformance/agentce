---
title: IntegrityBlock
description: Per-event integrity envelope (SPEC 6.6).
---

Per-event integrity envelope (SPEC 6.6).

| Attribute | Range | Required | Description |
|---|---|---|---|
| `hash` | string | yes |  |
| `prev` | string | yes |  |
| `stream` | string | yes |  |
| `strength` | IntegrityStrength | yes |  |
| `sig_ref` | string |  |  |
