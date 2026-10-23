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


def _load_text(tmp_path: Path, text: str) -> DomainBinding:
    path = tmp_path / "domain.yaml"
    path.write_text(text, encoding="utf-8")
    return DomainBinding.load(path)


def _scope_from_yaml(tmp_path: Path, scope: str) -> TaskScope:
    return _load_text(
        tmp_path, f"task_scopes:\n  - {{task_type: a, {scope}}}\n"
    ).task_scopes["a"][0]


def _assert_outside_profile(tmp_path: Path, text: str, rule: str, line: int) -> None:
    """``text`` is refused as the domain binding's key, naming the SPEC 8.7 profile ``rule`` and ``line``."""
    with pytest.raises(InputError) as caught:
        _load_text(tmp_path, text)
    assert caught.value.key == "input.domain_binding_invalid"
    assert (
        f"is outside the YAML profile: {rule} at line {line} (SPEC 8.7)"
        in caught.value.cause
    )


def _assert_scope_outside_profile(tmp_path: Path, scope: str, rule: str) -> None:
    _assert_outside_profile(
        tmp_path, f"task_scopes:\n  - {{task_type: a, {scope}}}\n", rule, 2
    )


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
        ("0x10", 16),
        ('"1e1"', None),
        ("2.5", 2.5),
    ],
)
def test_budget_limit_spellings_in_the_profile_are_read(
    tmp_path: Path, value: str, limit: float | None
) -> None:
    """A quoted number is text, so the limit is unreadable (18.184g)."""
    assert _scope_from_yaml(tmp_path, f"budgets: {{spend: {value}}}").budgets == (
        ("spend", limit),
    )


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("1_0", "underscore_number"),
        ("010", "octal"),
        ("1e1", "exponent_no_dot"),
        ("1:30", "sexagesimal"),
    ],
)
def test_a_yaml11_budget_limit_spelling_is_refused_outside_the_profile(
    tmp_path: Path, value: str, rule: str
) -> None:
    """A YAML 1.1 number spelling the engines would read differently is refused, never typed (SPEC 8.7)."""
    _assert_scope_outside_profile(tmp_path, f"budgets: {{spend: {value}}}", rule)


@pytest.mark.parametrize("value", ["yes", "on", "off", "n"])
def test_an_allowed_tools_yaml11_boolean_is_refused_outside_the_profile(
    tmp_path: Path, value: str
) -> None:
    """A YAML 1.1 boolean is refused (SPEC 8.7); quoted it is a tool name (18.184g)."""
    _assert_scope_outside_profile(tmp_path, f"allowed_tools: [{value}]", "yaml11_bool")
    assert _scope_from_yaml(
        tmp_path, f'allowed_tools: ["{value}"]'
    ).allowed_tools == frozenset({value})


@pytest.mark.parametrize(
    "text",
    [
        'task_scopes:\n  - {task_type: a, allowed_tools: ["x"], allowed_tools: ["y"]}\n',
        "task_scopes:\n  - {task_type: a, budgets: {spend: 1, spend: 2}}\n",
        "task_scopes: []\ntask_scopes: []\n",
        "task_scopes: !!python/object:os.system {}\n",
    ],
)
def test_repeated_key_is_refused(tmp_path: Path, text: str) -> None:
    """A key written twice, at any depth, is refused with one stable key, never the last value, as is a Python
    tag (18.184g)."""
    with pytest.raises(InputError) as caught:
        _load_text(tmp_path, text)
    assert caught.value.key == "input.domain_binding_invalid"


@pytest.mark.parametrize(
    ("text", "rule", "line"),
    [
        (
            "base: &b {task_type: a, allowed_tools: [x]}\ntask_scopes:\n  - {<<: *b, allowed_tools: [z]}\n",
            "anchor",
            1,
        ),
        (
            "task_scopes:\n  - {<<: {task_type: a, allowed_tools: [x]}, allowed_tools: [z]}\n",
            "merge_key",
            2,
        ),
        (
            "task_scopes:\n  - {allowed_tools: [z], <<: {task_type: a}, budgets: {tokens: 5}}\n",
            "merge_key",
            2,
        ),
    ],
)
def test_a_merge_key_or_anchor_is_refused_outside_the_profile(
    tmp_path: Path, text: str, rule: str, line: int
) -> None:
    """A << merge, with or without an anchor, is refused (SPEC 8.7): which key wins is never read."""
    _assert_outside_profile(tmp_path, text, rule, line)


@pytest.mark.parametrize(
    "text",
    [
        "b: &b {task_type: a}\ntask_scopes:\n  - {<<: *b, <<: {allowed_tools: [x]}}\n",
        "task_scopes:\n  - task_type: a\n    budgets:\n      <<: {tokens: 1000}\n      <<: {tokens: 10}\n",
        "<<: {x: 1}\n!!merge y: {x: 2}\n",
    ],
)
def test_merge_key_written_twice_is_refused(tmp_path: Path, text: str) -> None:
    """Two merge keys in one mapping are refused, as any key written twice is: which merge wins is never read
    (18.184k D1)."""
    with pytest.raises(InputError) as caught:
        _load_text(tmp_path, text)
    assert caught.value.key == "input.domain_binding_invalid"


def test_the_merged_keys_written_out_in_full_are_read(tmp_path: Path) -> None:
    """The profile form of a merge: every key written once, in full (SPEC 8.7)."""
    binding = _load_text(
        tmp_path,
        "task_scopes:\n  - {allowed_tools: [z], task_type: a, budgets: {tokens: 5}}\n",
    )
    scope = binding.task_scopes["a"][0]
    assert scope.allowed_tools == frozenset({"z"})
    assert dict(scope.budgets)["tokens"] == 5


def test_yaml_dates_are_neither_text_nor_numbers(tmp_path: Path) -> None:
    """A YAML date names no tool, sets no limit and no task type, and renders as str(date) where an id is read
    (18.184g: the reference the TypeScript and Java readers match)."""
    assert _scope_from_yaml(
        tmp_path, "allowed_tools: [2026-01-01, x]"
    ).allowed_tools == {"x"}
    assert _scope_from_yaml(
        tmp_path, "budgets: {tokens: 2020-01-01, spend: 2026-01-01T10:00:00}"
    ).budgets == (("tokens", None), ("spend", None))
    assert _scope_from_yaml(tmp_path, "budgets: 2026-01-01").budgets == (
        ("tokens", None),
        ("compute_seconds", None),
        ("spend", None),
    )
    malformed = _load_text(
        tmp_path, "task_scopes:\n  - {task_type: 2026-01-01}\n  - 2026-01-01\n"
    )
    assert malformed.task_scopes == {} and malformed.task_scopes_malformed
    ids = _load_text(
        tmp_path,
        "decision_types:\n  - {id: 2026-01-01, subclass_of: 2026-01-02T03:04:05Z,"
        " required_oversight_modality: 2026-01-03}\n"
        "context_classes:\n  - {id: 2026-01-01 10:00:00}\n",
    )
    assert ids.subclasses == {
        "2026-01-01": "2026-01-02 03:04:05+00:00",
        "2026-01-01 10:00:00": CONTEXT_ROOT,
    }
    assert ids.required_oversight == {}


def test_limit_past_float_range_is_unreadable(tmp_path: Path) -> None:
    """A 400-digit limit is unreadable, as TypeScript and Java read it (infinity), never a crash (18.184g)."""
    huge = "9" * 400
    assert _scope_from_yaml(
        tmp_path, f"budgets: {{spend: {huge}, compute_seconds: {huge}}}"
    ).budgets == (("compute_seconds", None), ("spend", None))


@pytest.mark.parametrize(
    "scope",
    [
        'allowed_tools: [!!bool "yes"]',
        'allowed_tools: [!!int "7"]',
        'allowed_tools: [!!float "1.5"]',
        "allowed_tools: [!!str 7]",
        "allowed_tools: [!!str yes]",
        "budgets: {tokens: !!str 1000}",
        'budgets: {spend: !!int "10"}',
        'budgets: {compute_seconds: !!float "5"}',
        'budgets: {tokens: !!int "0o10"}',
        'budgets: {compute_seconds: !!float "+.5"}',
        'budgets: {spend: !!float "inf"}',
        'budgets: {tokens: !!int " 5"}',
    ],
)
def test_an_explicit_core_tag_is_refused_outside_the_profile(
    tmp_path: Path, scope: str
) -> None:
    """A tag never decides a type (SPEC 8.7), so no engine has to match another's int()/float() reading."""
    _assert_scope_outside_profile(tmp_path, scope, "tag")


def test_the_untagged_profile_spellings_are_read(tmp_path: Path) -> None:
    """Quoting keeps a value text and a plain number is a number, with no tag (SPEC 8.7, 18.184g)."""
    scope = _scope_from_yaml(tmp_path, 'allowed_tools: ["yes", "7", x]')
    assert scope.allowed_tools == {"7", "x", "yes"}
    assert _scope_from_yaml(
        tmp_path, 'budgets: {tokens: "1000", spend: 10, compute_seconds: 5.0}'
    ).budgets == (("tokens", None), ("compute_seconds", 5.0), ("spend", 10))


def test_a_date_no_calendar_holds_is_text(tmp_path: Path) -> None:
    """The reference Java and TypeScript match (18.184g)."""
    assert _scope_from_yaml(
        tmp_path, "allowed_tools: [2021-02-29, 2020-02-29]"
    ).allowed_tools == {"2021-02-29"}


def test_a_date_written_as_a_key_is_refused_outside_the_profile(
    tmp_path: Path,
) -> None:
    """A plain date key is not a string key (SPEC 8.7); quoted, it is a key no budget reads."""
    _assert_scope_outside_profile(
        tmp_path, "budgets: {2020-01-01: 1, 2020-01-02: 2}", "non_string_key"
    )
    assert (
        _scope_from_yaml(
            tmp_path, 'budgets: {"2020-01-01": 1, "2020-01-02": 2}'
        ).budgets
        == ()
    )


@pytest.mark.parametrize(
    "scope",
    [
        "allowed_tools: !!str [x]",
        "task_type_x: !!seq x",
        "allowed_tools: [<<]",
        'budgets: {tokens: !!int "1:"}',
        'budgets: {spend: !!float "5f"}',
    ],
)
def test_a_tag_the_node_cannot_take_is_refused(tmp_path: Path, scope: str) -> None:
    """The reference Java and TypeScript match (18.184g)."""
    with pytest.raises(InputError) as exc:
        _scope_from_yaml(tmp_path, scope)
    assert exc.value.key == "input.domain_binding_invalid"
