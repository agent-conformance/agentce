# `Payload`

Common members of every event payload (SPEC 6.2.2).

This is an abstract base class; it is never emitted directly.

| Attribute | Range | Required | Description |
|---|---|---|---|
| `agent` | AgentRef |  |  |
| `acted_for` | string |  |  |
| `session_id` | string |  |  |
| `refs` | Refs |  |  |
| `integrity` | IntegrityBlock |  |  |
| `ext` | string |  | Source-specific extras; never evaluated by rules. |
