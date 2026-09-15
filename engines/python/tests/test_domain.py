"""The domain binding loader (SPEC §6.5)."""

from __future__ import annotations

from pathlib import Path

from agentce.domain import CONTEXT_ROOT, DECISION_ROOT, DomainBinding


def test_empty_binding() -> None:
    binding = DomainBinding.empty()
    assert binding.subclasses == {}
    assert binding.consequential == set()
    assert binding.required_oversight == {}


def test_from_dict_decision_and_context() -> None:
    binding = DomainBinding.from_dict(
        {
            "decision_types": [
                {
                    "id": "agentce:Credit",
                    "consequential": True,
                    "required_oversight_modality": "review_before",
                },
                {"id": "agentce:Minor"},
            ],
            "context_classes": [{"id": "agentce:CreditFile"}],
        }
    )
    assert binding.subclasses["agentce:Credit"] == DECISION_ROOT
    assert binding.subclasses["agentce:CreditFile"] == CONTEXT_ROOT
    assert binding.consequential == {"agentce:Credit"}
    assert binding.required_oversight == {"agentce:Credit": "review_before"}


def test_from_dict_ignores_malformed_entries() -> None:
    binding = DomainBinding.from_dict({"decision_types": ["nope", {"no_id": 1}]})
    assert binding.subclasses == {}


def test_load_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "domain.yaml"
    path.write_text(
        "decision_types:\n  - id: agentce:Credit\n    subclass_of: agentce:ConsequentialDecision\n"
        "    consequential: true\n",
        encoding="utf-8",
    )
    binding = DomainBinding.load(path)
    assert binding.subclasses == {"agentce:Credit": "agentce:ConsequentialDecision"}
    assert binding.consequential == {"agentce:Credit"}


def test_load_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert DomainBinding.load(path).subclasses == {}
