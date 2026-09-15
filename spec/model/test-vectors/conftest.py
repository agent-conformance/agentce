"""Put the model directory on sys.path so the vector tests can import the reference canonicaliser.

pytest imports this file before collecting the tests in this directory, so `import canonical` at the
top of test_vectors.py resolves without any in-module path manipulation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
