"""Agent Conformance Engine (AgentCE) — Python reference engine.

A deterministic, read-only, model-free engine that evaluates the evidence an AI-agent deployment
produces against executable control catalogs and emits a conformance report. This package provides
the command-line interface skeleton (SPEC §8.5): the command set, the common exit-code scheme,
``--json`` output on every command, and structured logging. The evaluation stages (ingest,
integrity, graph, coverage, applicability, structural evaluation, reporting) are layered on in the
subsequent work items.
"""

from __future__ import annotations

#: Engine implementation identifier reported by ``agentce version`` and the conformance suite.
ENGINE_NAME = "agentce-py"

#: Package version.
__version__ = "0.0.1"

#: Specification draft this engine targets (SPEC front matter).
SPEC_VERSION = "0.6"

__all__ = ["ENGINE_NAME", "SPEC_VERSION", "__version__"]
