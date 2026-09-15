"""The deterministic AgentCE corpus generator (SPEC §11.2–11.4).

Runnable two ways over the same self-contained :mod:`~corpus.generator.generate` module: as the
package entrypoint ``python -m corpus.generator --out <dir>`` (the interface the phase-1 eval calls)
and as the standalone script ``python generate.py --set v1 --out <dir>`` (the item's acceptance).
"""

from __future__ import annotations

from .generate import build_corpus, main

__all__ = ["build_corpus", "main"]
