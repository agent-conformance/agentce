"""The AgentCE simulated corpus package (SPEC §11).

Holds the deterministic generator (:mod:`corpus.generator`) and the single-project assess helper
(:mod:`corpus.assess_one`). The generated output itself is produced on demand and lives on the
dataset host, never in the repository (SPEC §11.7).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.1"
