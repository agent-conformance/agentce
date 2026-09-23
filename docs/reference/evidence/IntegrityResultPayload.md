# `IntegrityResultPayload`

Engine-computed verification outcome per stream (SPEC 6.6).

Extends `Payload`.

| Attribute | Range | Required | Description |
|---|---|---|---|
| `stream` | string |  |  |
| `strength` | IntegrityStrength |  |  |
| `status` | IntegrityStatus |  |  |
| `first_bad_index` | integer |  |  |
| `anchors` | string |  |  |
