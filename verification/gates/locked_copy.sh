#!/usr/bin/env bash
# Build gate helper for VG-LOCKED-COPY: the site builds, and the built landing page, the README and every
# engine README carry the approved product copy word for word (no retired wording, "conforming engine"
# among it, per CP-1 in governance/CONFORMANCE-PROGRAM.md, loophole L18.10).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root" && python3 tools/locked_copy_check.py --self-test)
(cd "$root/website" && pnpm build >/dev/null)
(cd "$root" && python3 tools/locked_copy_check.py --dist website/dist --readme README.md --also engines/README.md engines/*/README.md)
