#!/usr/bin/env bash
# Build gate for VG-CATALOG-READ-ONCE (18.171): every engine checks a --catalog-dir catalog's signature over the same
# bytes it then loads, reads each covered file once, and reports and packages those bytes (SPEC §8.7, §14.5 CP-3).
# The driver signs a copy of the baseline catalog and runs the real Python, TypeScript and Java CLIs with a writer
# racing them: once the engine has read DOC-01.yaml, any later read of it sees other bytes.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=verification/gates/build_engines.sh
. "$root/verification/gates/build_engines.sh"
build_engines
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python "$root/verification/gates/catalog_read_once_check.py" "$@")
