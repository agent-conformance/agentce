"""A sample adapters orchestrator for VG-CLI-UTILITY: a full report with a byte that is not UTF-8 in its
stdout, which Python's subprocess.run(text=True) refuses to decode (internal.unexpected)."""

import sys

sys.stdout.buffer.write(b'{"adapters": ["sample\xff"], "total": 1, "identical": 1, "round_trip": true}\n')
