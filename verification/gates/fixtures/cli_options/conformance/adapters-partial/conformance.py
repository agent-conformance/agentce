"""A sample adapters orchestrator for VG-CLI-UTILITY, in place of adapters/conformance.py: prints the
--json report the real one prints for one adapter, identical but failing its round trip, so the adapters claim is partial. With --out <dir> it writes the per-adapter
implementation report where the real one does, so the gate can see the engine passed --out through."""

import json
import sys
from pathlib import Path

entry = {"adapter": "sample", "total": 1, "identical": 1, "round_trip": False}
args = sys.argv[1:]
if "--out" in args:
    target = Path(args[args.index("--out") + 1]) / "adapters" / "sample"
    target.mkdir(parents=True, exist_ok=True)
    (target / "implementation-report.json").write_text(
        json.dumps(entry, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
report = {k: v for k, v in entry.items() if k != "adapter"}
print(json.dumps({"adapters": ["sample"], **report, "by_adapter": [entry]}))
