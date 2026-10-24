#!/usr/bin/env bash
# Build gate for VG-OUT-LINK-CONTAINMENT (18.184s): a run never writes through a link in --out that leads outside it
# (SPEC 8.7, ruling R7). The real Python, TypeScript and Java CLIs run quickstart --out with integrity.jsonl,
# manifest.json or the folder packs linked outside, and each must refuse input.out_link_outside with nothing written.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

(cd engines/typescript && pnpm install --frozen-lockfile >/dev/null && pnpm --silent build >/dev/null)
(cd engines/java && ./gradlew --no-daemon --quiet :assemble installDist)

env -u VIRTUAL_ENV uv run --project tools --frozen python tools/out_link_containment_check.py
