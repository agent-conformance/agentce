#!/usr/bin/env bash
# Build gate helper for VG-CRYPTO-INTEL-WHEEL: a later cryptography bump that drops the Intel-macOS
# wheel split (ADR-0024) from any tracked uv.lock must turn this gate red, not wait for a fresh-install
# failure on an Intel Mac to surface it (item 18.58, 18.55's O1).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
uv run --project tools --frozen python tools/cryptography_intel_wheel_check.py --self-test
uv run --project tools --frozen python tools/cryptography_intel_wheel_check.py
