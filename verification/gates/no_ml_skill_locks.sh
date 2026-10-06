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
uv run --project tools --frozen python - <<'PY'
import sys
from pathlib import Path

import yaml

steps = yaml.safe_load(Path(".github/workflows/no-ml.yml").read_text(encoding="utf-8"))["jobs"]["no-ml"]["steps"]
runs = [step.get("run", "") for step in steps]
vendor = next((i for i, r in enumerate(runs) if "tools/vendor_skill_engine.py --write" in r), None)
scan = next((i for i, r in enumerate(runs) if "tools/no_ml_check.py --require-skill-locks" in r), None)
if vendor is None or scan is None or vendor > scan:
    print(
        "FAIL: no-ml.yml must run `python3 tools/vendor_skill_engine.py --write` in a step before the "
        f"step running `no_ml_check.py --require-skill-locks` (vendor step {vendor}, scan step {scan})"
    )
    sys.exit(1)
print(f"no-ml.yml: vendor step {vendor} runs before scan step {scan}")
PY
python3 tools/vendor_skill_engine.py --write >/dev/null
python3 tools/no_ml_check.py --require-skill-locks
