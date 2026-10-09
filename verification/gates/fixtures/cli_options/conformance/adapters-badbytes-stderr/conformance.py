"""A sample adapters orchestrator for VG-CLI-UTILITY: a full report, and a byte that is not UTF-8 on
stderr, which Python's subprocess.run(text=True) refuses to decode (internal.unexpected)."""

import sys

print('{"adapters": ["sample"], "total": 1, "identical": 1, "round_trip": true}')
sys.stderr.buffer.write(b"warning \xff\n")
