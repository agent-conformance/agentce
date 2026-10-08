"""A sample adapters orchestrator for VG-CLI-UTILITY that prints no JSON: the engine records an error in
place of the report and the adapters claim is none."""

import sys

print("adapter conformance could not run")
print("no adapters found", file=sys.stderr)
