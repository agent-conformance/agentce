---
title: SessionStartPayload
description: The `SessionStartPayload` evidence-model class.
---

Extends `Payload`.

| Attribute | Range | Required | Description |
|---|---|---|---|
| `deployer` | Principal |  |  |
| `environment` | string |  |  |
| `bundle_digest` | string |  |  |
| `model_versions` | string |  |  |
| `intended_purpose_ref` | string |  |  |
| `registration_ref` | string |  |  |
| `principal` | Principal |  | The human an identity provider authenticated in this session. An approval, override or interrupt counts as a human's only when the login its session_ref names gives that same human here (SPEC 10.4). |
