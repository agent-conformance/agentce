"""The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes.

The binding subclasses ``agentce:Decision`` and ``agentce:ContextItem`` for the domain and declares
which decision types are consequential and the oversight modality each requires. The graph builder
uses it to materialise ``agentce:executesConsequential`` and ``agentce:oversightModalityMatchesDeclared``
and to extend the ``rdfs:subClassOf*`` closure. A run with no binding is valid (an empty binding).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DECISION_ROOT = "agentce:Decision"
CONTEXT_ROOT = "agentce:ContextItem"


@dataclass
class DomainBinding:
    """A parsed domain binding."""

    #: child class -> parent class (domain subclasses of Decision / ContextItem).
    subclasses: dict[str, str] = field(default_factory=dict)
    #: decision-type IRIs declared consequential.
    consequential: set[str] = field(default_factory=set)
    #: decision-type IRI -> the oversight modality it requires.
    required_oversight: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> DomainBinding:
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainBinding:
        binding = cls()
        for entry in data.get("decision_types", []) or []:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            decision_id = str(entry["id"])
            binding.subclasses[decision_id] = str(
                entry.get("subclass_of", DECISION_ROOT)
            )
            if entry.get("consequential"):
                binding.consequential.add(decision_id)
            modality = entry.get("required_oversight_modality")
            if isinstance(modality, str):
                binding.required_oversight[decision_id] = modality
        for entry in data.get("context_classes", []) or []:
            if isinstance(entry, dict) and "id" in entry:
                binding.subclasses[str(entry["id"])] = str(
                    entry.get("subclass_of", CONTEXT_ROOT)
                )
        return binding

    @classmethod
    def load(cls, path: Path) -> DomainBinding:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return cls.empty()
        return cls.from_dict(data)
