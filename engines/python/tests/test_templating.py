"""The minimal Mustache-style renderer `remediation.md` is built from (SPEC §7)."""

from __future__ import annotations

from agentce.templating import render


def test_variable_interpolation() -> None:
    assert render("hello {{name}}", {"name": "world"}) == "hello world"


def test_missing_variable_renders_empty() -> None:
    assert render("[{{missing}}]", {}) == "[]"


def test_dotted_path_variable() -> None:
    assert render("{{a.b}}", {"a": {"b": "Z"}}) == "Z"


def test_dotted_path_missing_inner_key_renders_empty() -> None:
    assert render("[{{a.c}}]", {"a": {"b": "Z"}}) == "[]"


def test_section_iterates_a_list_pushing_each_item_as_scope() -> None:
    out = render(
        "{{#items}}<{{name}}>{{/items}}", {"items": [{"name": "x"}, {"name": "y"}]}
    )
    assert out == "<x><y>"


def test_section_current_item_dot() -> None:
    out = render("{{#items}}{{.}}-{{/items}}", {"items": ["a", "b"]})
    assert out == "a-b-"


def test_section_empty_list_renders_nothing() -> None:
    assert render("[{{#items}}x{{/items}}]", {"items": []}) == "[]"


def test_section_on_truthy_dict_renders_once_with_pushed_scope() -> None:
    assert render("{{#a}}{{b}}{{/a}}", {"a": {"b": "Z"}}) == "Z"


def test_section_on_empty_dict_renders_nothing() -> None:
    assert render("[{{#a}}x{{/a}}]", {"a": {}}) == "[]"


def test_inverted_section_renders_only_when_falsy() -> None:
    assert render("{{^items}}none{{/items}}", {"items": []}) == "none"
    assert render("{{^items}}none{{/items}}", {"items": ["x"]}) == ""


def test_nested_sections() -> None:
    ctx = {"outer": [{"inner": ["a", "b"]}]}
    out = render("{{#outer}}[{{#inner}}{{.}}{{/inner}}]{{/outer}}", ctx)
    assert out == "[ab]"


def test_inner_scope_shadows_outer_scope() -> None:
    ctx = {"name": "outer", "items": [{"name": "inner"}]}
    out = render("{{#items}}{{name}}{{/items}}", ctx)
    assert out == "inner"


def test_text_outside_tags_is_verbatim() -> None:
    assert render("a\nb\tc", {}) == "a\nb\tc"


def test_pure_function_of_its_arguments() -> None:
    template = "{{#items}}{{.}},{{/items}}{{tail}}"
    ctx = {"items": ["x", "y"], "tail": "z"}
    assert render(template, ctx) == render(template, dict(ctx))
