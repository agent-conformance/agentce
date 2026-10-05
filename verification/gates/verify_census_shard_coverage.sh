#!/usr/bin/env bash
# Build gate helper for VG-VERIFY-CENSUS-SHARD-COVERAGE: quickstart's verify-census matrix (18.93)
# splits the cross-engine `agentce verify` census across shards it computes at run time, so a
# workflow edit that rewires the shard step, drops a shard, or leaves the full census in another job,
# or a selection bug in tools/verify_parity_check.py, could silently stop running some mutations.
# This checks the real workflow's wiring and the real --list-shard output against the full list.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/verify_census_shard_coverage_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/verify_census_shard_coverage_check.py
