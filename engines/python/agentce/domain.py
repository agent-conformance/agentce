"""The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes.

The binding subclasses ``agentce:Decision`` and ``agentce:ContextItem`` for the domain and declares
which decision types are consequential and the oversight modality each requires; for the Conduct
overlay it declares each task type's effect scope (SPEC §7.7.6 ``task_scopes``). The graph builder
uses it to materialise ``agentce:executesConsequential``, ``agentce:oversightModalityMatchesDeclared``
and ``agentce:withinScope`` and to extend the ``rdfs:subClassOf*`` closure. A run with no binding is
valid (an empty binding).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .safe_yaml import load_untrusted_yaml

DECISION_ROOT = "agentce:Decision"
CONTEXT_ROOT = "agentce:ContextItem"


def _strings(value: Any) -> frozenset[str]:
    """The string members of a declared list; anything else declares none."""
    return (
        frozenset(v for v in value if isinstance(v, str))
        if isinstance(value, list)
        else frozenset()
    )


@dataclass(frozen=True)
class TaskScope:
    """One task type's declared effect scope (SPEC §7.7.6), as CND-01 reads it: the tools and the
    effect classes an action of the task may use. ``requires_approval`` marks classes for CND-02 and
    allows none by itself."""

    #: None when the scope lists no tools (any tool); otherwise the tools it allows.
    allowed_tools: frozenset[str] | None
    #: the effect classes it allows; none when the scope lists none.
    allowed_effect_classes: frozenset[str]
    #: the effect classes that need a human's approval (CND-02's marker; allows none for CND-01).
    requires_approval: frozenset[str] = frozenset()

    def allows(self, tool: Any, effect_class: Any) -> bool:
        if self.allowed_tools is not None and tool not in self.allowed_tools:
            return False
        return effect_class in self.allowed_effect_classes


@dataclass
class DomainBinding:
    """A parsed domain binding."""

    #: child class -> parent class (domain subclasses of Decision / ContextItem).
    subclasses: dict[str, str] = field(default_factory=dict)
    #: decision-type IRIs declared consequential.
    consequential: set[str] = field(default_factory=set)
    #: decision-type IRI -> the oversight modality it requires.
    required_oversight: dict[str, str] = field(default_factory=dict)
    #: task type -> every scope declared for it, in order (an action must stay within all of them).
    task_scopes: dict[str, list[TaskScope]] = field(default_factory=dict)

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
        scopes = data.get("task_scopes")
        for entry in scopes if isinstance(scopes, list) else []:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("task_type"), str
            ):
                continue
            tools = entry.get("allowed_tools")
            binding.task_scopes.setdefault(entry["task_type"], []).append(
                TaskScope(
                    None if tools is None else _strings(tools),
                    _strings(entry.get("allowed_effect_classes")),
                    _strings(entry.get("requires_approval")),
                )
            )
        return binding

    def scopes_for(self, task: str) -> list[TaskScope]:
        """The scopes a task's actions are held to: those declared for the task type the task names,
        else, when the binding declares a single task type, that type's (every task is of it), else
        none, so nothing the task does can be shown within a declared scope."""
        if task in self.task_scopes:
            return self.task_scopes[task]
        if len(self.task_scopes) == 1:
            return next(iter(self.task_scopes.values()))
        return []

    @classmethod
    def load(cls, path: Path) -> DomainBinding:
        data = (
            load_untrusted_yaml(
                path, key="input.domain_binding_invalid", what="the domain binding"
            )
            or {}
        )
        if not isinstance(data, dict):
            return cls.empty()
        return cls.from_dict(data)
