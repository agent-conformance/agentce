#!/usr/bin/env bash
# Build gate helper for VG-ENGINE-GOLDENS-FRESH (18.37d): every TypeScript and Java parity golden is
# what the live Python engine writes today, so a Python-only change cannot leave the ports' byte-identical
# tests green on a stale snapshot. The self-test proves each generator's --check refuses an edited, a
# deleted and an unproduced golden and writes nothing; the real run checks both engines' testdata.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/engine_goldens_fresh_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/engine_goldens_fresh_check.py
