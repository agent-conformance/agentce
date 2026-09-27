#!/usr/bin/env bash
# Build gate helper for VG-LOCKED-COPY: the site builds, and the built landing page and the README carry
# the approved product copy word for word.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root" && python3 tools/locked_copy_check.py --self-test)
(cd "$root/website" && pnpm build >/dev/null)
(cd "$root" && python3 tools/locked_copy_check.py --dist website/dist --readme README.md)
