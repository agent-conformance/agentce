"""The domain binding loader (SPEC §6.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce.domain import CONTEXT_ROOT, DECISION_ROOT, DomainBinding, TaskScope
from agentce.errors import InputError


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


def test_task_scope_parsing() -> None:
    binding = DomainBinding.from_dict(
        {
            "task_scopes": [
                {
                    "task_type": "a",
                    "allowed_tools": ["t", 1],
                    "allowed_effect_classes": ["read"],
                    "requires_approval": ["spend", None],
                },
                {"task_type": "a", "allowed_effect_classes": "read"},
                {"allowed_effect_classes": ["read"]},
                "nope",
            ]
        }
    )
    assert list(binding.task_scopes) == ["a"]
    first, second = binding.task_scopes["a"]
    assert first.allowed_tools == {"t"} and first.allowed_effect_classes == {"read"}
    assert second.allowed_tools is None and second.allowed_effect_classes == frozenset()
    assert (
        first.requires_approval == {"spend"} and second.requires_approval == frozenset()
    )
    assert not first.allows("t", "spend")
    assert first.allows("t", "read") and not first.allows("u", "read")
    assert not first.allows("t", "write") and not second.allows("t", "read")


def test_task_scope_not_a_list_declares_none() -> None:
    assert DomainBinding.from_dict({"task_scopes": {"a": {}}}).task_scopes == {}
    assert DomainBinding.from_dict({"task_scopes": 5}).task_scopes == {}


def test_task_scope_resolution() -> None:
    one = DomainBinding.from_dict({"task_scopes": [{"task_type": "a"}]})
    two = DomainBinding.from_dict(
        {"task_scopes": [{"task_type": "a"}, {"task_type": "b"}]}
    )
    assert one.scopes_for("task-1") == one.task_scopes["a"]
    assert two.scopes_for("b") == two.task_scopes["b"]
    assert (
        two.scopes_for("task-1") == [] and DomainBinding.empty().scopes_for("a") == []
    )


def test_task_scope_unreadable_entry_keeps_single_type_from_standing_for_all() -> None:
    for bad in ({"task-type": "2024"}, {"task_type": 2024}, "nope"):
        binding = DomainBinding.from_dict(
            {"task_scopes": [bad, {"task_type": "batch"}]}
        )
        assert binding.task_scopes_malformed and list(binding.task_scopes) == ["batch"]
        assert binding.scopes_for("2024") == []
        assert binding.scopes_for("batch") == binding.task_scopes["batch"]
    assert not DomainBinding.from_dict(
        {"task_scopes": [{"task_type": "a"}]}
    ).task_scopes_malformed


def test_task_scope_budgets() -> None:
    """The budgets a scope declares (18.175): a non-negative number per known dimension, None when unreadable."""
    binding = DomainBinding.from_dict(
        {
            "task_scopes": [
                {
                    "task_type": "a",
                    "budgets": {
                        "spend": 0,
                        "tokens": 10,
                        "cost": 3,
                        "compute_seconds": "60",
                    },
                },
                {"task_type": "b", "budgets": [1]},
                {"task_type": "c", "budgets": {"tokens": True, "spend": -1}},
                {"task_type": "d"},
            ]
        }
    )
    assert binding.task_scopes["a"][0].budgets == (
        ("tokens", 10),
        ("compute_seconds", None),
        ("spend", 0),
    )
    assert binding.task_scopes["b"][0].budgets == (
        ("tokens", None),
        ("compute_seconds", None),
        ("spend", None),
    )
    assert binding.task_scopes["c"][0].budgets == (("tokens", None), ("spend", None))
    assert binding.task_scopes["d"][0].budgets == ()


def _scope_from_yaml(tmp_path: Path, scope: str) -> TaskScope:
    path = tmp_path / "domain.yaml"
    path.write_text(f"task_scopes:\n  - {{task_type: a, {scope}}}\n", encoding="utf-8")
    return DomainBinding.load(path).task_scopes["a"][0]


@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan"])
@pytest.mark.parametrize("dimension", ["spend", "compute_seconds", "tokens"])
def test_non_finite_budget_limit_is_unreadable(
    tmp_path: Path, dimension: str, value: str
) -> None:
    """A non-finite limit is unreadable in every dimension (18.184g): never a limit nothing exceeds."""
    scope = _scope_from_yaml(tmp_path, f"budgets: {{{dimension}: {value}}}")
    assert scope.budgets == ((dimension, None),)


@pytest.mark.parametrize(
    ("value", "limit"),
    [
        ("10", 10),
        ("1_0", 10),
        ("010", 8),
        ("0x10", 16),
        ("1e1", None),
        ('"1e1"', None),
        ("2.5", 2.5),
    ],
)
def test_budget_limit_spellings_read_as_pyyaml_types_them(
    tmp_path: Path, value: str, limit: float | None
) -> None:
    """YAML 1.1 number spellings (18.184g): 1e1 without a dot is a string to PyYAML, so the limit is unreadable."""
    assert _scope_from_yaml(tmp_path, f"budgets: {{spend: {value}}}").budgets == (
        ("spend", limit),
    )


def test_allowed_tools_yaml11_booleans_are_not_tool_names(tmp_path: Path) -> None:
    """[yes, on, off] are booleans to PyYAML and name no tool; quoted they are names (18.184g)."""
    assert (
        _scope_from_yaml(tmp_path, "allowed_tools: [yes, on, off]").allowed_tools
        == frozenset()
    )
    assert _scope_from_yaml(
        tmp_path, 'allowed_tools: ["yes"]'
    ).allowed_tools == frozenset({"yes"})


@pytest.mark.parametrize(
    "text",
    [
        'task_scopes:\n  - {task_type: a, allowed_tools: ["x"], allowed_tools: ["y"]}\n',
        "task_scopes:\n  - {task_type: a, budgets: {spend: 1, spend: 2}}\n",
        "task_scopes: []\ntask_scopes: []\n",
    ],
)
def test_repeated_key_is_refused(tmp_path: Path, text: str) -> None:
    """A key written twice, at any depth, is refused with one stable key, never the last value (18.184g)."""
    path = tmp_path / "domain.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InputError) as caught:
        DomainBinding.load(path)
    assert caught.value.key == "input.domain_binding_invalid"


def test_merge_key_may_be_written_again(tmp_path: Path) -> None:
    """A key a << merge brings in may be set again, as PyYAML allows (18.184g)."""
    path = tmp_path / "domain.yaml"
    path.write_text(
        "base: &b {task_type: a, allowed_tools: [x]}\ntask_scopes:\n  - {<<: *b, allowed_tools: [y]}\n",
        encoding="utf-8",
    )
    assert DomainBinding.load(path).task_scopes["a"][0].allowed_tools == frozenset(
        {"y"}
    )
