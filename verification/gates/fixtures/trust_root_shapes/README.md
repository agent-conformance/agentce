# trust_root_shapes

Fixtures for `VG-I18N-ERROR-CATALOGUE` (18.80): one malformed trust root per shape, and the cause tail
each engine must print after "is not a usable trust root: " (`expected.txt`, `name|tail`). `empty-keys.json`
is well formed: an empty `keys` mapping is no keys, and every engine must give it the same exit code and
no `input.trust_root_invalid`. Implements SPEC §8.7 (`--trust-root`) and §13.4 AX-6 (stable message keys).
