#!/usr/bin/env bash
# Build gate helper for VG-NO-ML-SKILL-LOCKS: the no-ml job must scan the two skills' dependency trees,
# whose uv.lock files are gitignored since 18.54. The gate proves the scan refuses a missing,
# empty or unrelated skill lock, that no-ml.yml generates the locks in a step before the scan step that
# requires them, and that the real scan passes with the locks required (item 18.56). Generating the
# locks needs uv and the package index, as skill_release_shape.sh's --release does.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
python3 tools/no_ml_check.py --self-test
python3 - <<'PY'
import sys
from pathlib import Path

steps = Path(".github/workflows/no-ml.yml").read_text(encoding="utf-8").split("\n      - ")
vendor = [i for i, s in enumerate(steps) if "run: python3 tools/vendor_skill_engine.py --write" in s]
scan = [i for i, s in enumerate(steps) if "tools/no_ml_check.py --require-skill-locks" in s]
if not vendor or not scan or min(vendor) > min(scan):
    print(
        "FAIL: no-ml.yml must run `python3 tools/vendor_skill_engine.py --write` in a step before the "
        f"step running `no_ml_check.py --require-skill-locks` (vendor steps {vendor}, scan steps {scan})"
    )
    sys.exit(1)
print(f"no-ml.yml: vendor step {min(vendor)} runs before scan step {min(scan)}")
PY
python3 tools/vendor_skill_engine.py --write >/dev/null
python3 tools/no_ml_check.py --require-skill-locks
