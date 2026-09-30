#!/usr/bin/env bash
# Build gate helper for VG-SKILL-RELEASE-SHAPE: once everyday commits stop tracking the two agent
# skills' vendored engine wheel and lock, the commit a release tag points at still force-adds and
# commits them, so a commit-pinned checkout of that tag keeps installing standalone and offline
# exactly as skills/README.md's "Install and pin (S-10)" documents.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
uv run --project tools --frozen python tools/skill_release_shape_check.py --self-test
