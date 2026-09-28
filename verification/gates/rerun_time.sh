#!/usr/bin/env bash
# Build gate helper for VG-RERUN-TIME: a sender packages and signs a shareable report bundle from
# corpus/quickstart; a separate recipient installs the engine fresh and `verify --report`s it offline,
# timed from before the recipient's own install, reproducing every canonical output byte for byte inside
# the ten-minute budget (Hill 3); a tampered report and a tampered evidence file are each refused.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root" && env -u VIRTUAL_ENV uv run --project tools --frozen python tools/installed_artifacts_check.py rerun)
