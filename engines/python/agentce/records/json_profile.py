"""The JSON profile reader the vendored ``otel_genai`` adapter imports (SPEC 8.7).

In the adapter package this module is a copy of :mod:`agentce.safe_json`; here it re-exports the
engine's one reader, so the vendored adapter stays byte-identical to its source.
"""

from ..safe_json import JsonProfileError, read_json

__all__ = ["JsonProfileError", "read_json"]
