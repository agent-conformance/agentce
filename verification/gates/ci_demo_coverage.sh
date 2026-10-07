#!/usr/bin/env bash
# Build gate helper for VG-DEMO-SHARD-COVERAGE: the CI `demo-fault` matrix (18.74) partitions the
# gate list across shards it computes at run time, so a workflow edit that drops or duplicates a
# shard index silently stops demoing one residue class of gates forever. This reconstructs that
# partition from the real registry and the real workflow file, checks the demo-fault step's and the
# quick job's own wiring (including quick's exact dependency-check run text) and the workflow root's
# keys (no root env: BASH_ENV or defaults, 18.76), and fails if any gate is uncovered or either job's
# wiring could silently stop proving anything.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/ci_demo_coverage_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/ci_demo_coverage_check.py
