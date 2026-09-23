"""A minimal, logic-less Mustache-style renderer (SPEC §7: one language-neutral template in `spec/`).

Supports exactly what ``spec/report/remediation.md.tmpl`` needs and nothing more: ``{{path}}``
variable interpolation (dotted paths resolved against a context stack, innermost scope first),
``{{#path}}...{{/path}}`` sections (iterate a list, pushing each item as a new scope; render once,
unchanged scope, for any other truthy value; render nothing for empty/falsy), ``{{^path}}...{{/path}}``
inverted sections (render only when the value is empty/falsy), and ``{{.}}`` for the current scope
itself (used to interpolate a list item that is a plain string, e.g. a re-verify argv element). No
Python is evaluated: the template can only select and repeat data already in the context, so an
instruction sentence can never be assembled from evidence-derived strings (SPEC §7 injection
hardening) -- the template supplies every word that is not a ``{{...}}`` substitution verbatim.
"""

from __future__ import annotations

import re
from typing import Any

_TAG_RE = re.compile(r"\{\{([#^/]?)([a-zA-Z0-9_.]+|\.)\}\}")

#: Every node is a 3-tuple ``(kind, value, children)``: ``children`` is ``None`` for a leaf
#: (``text``/``var``) and a child node list for a container (``section``/``inverted``) -- one fixed
#: shape, rather than a union of different-length tuples, so a type checker can follow it.
_Node = tuple[str, Any, "list[_Node] | None"]


def _parse(template: str) -> list[_Node]:
    root: list[_Node] = []
    stack: list[list[_Node]] = [root]
    open_names: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(template):
        if m.start() > pos:
            stack[-1].append(("text", template[pos : m.start()], None))
        sigil, name = m.group(1), m.group(2)
        if sigil == "#":
            children: list[_Node] = []
            stack[-1].append(("section", name, children))
            stack.append(children)
            open_names.append(name)
        elif sigil == "^":
            children = []
            stack[-1].append(("inverted", name, children))
            stack.append(children)
            open_names.append(name)
        elif sigil == "/":
            if not open_names or open_names[-1] != name:
                raise ValueError(f"mismatched closing tag {{/{name}}} in template")
            open_names.pop()
            stack.pop()
        else:
            stack[-1].append(("var", name, None))
        pos = m.end()
    if pos < len(template):
        stack[-1].append(("text", template[pos:], None))
    if open_names:
        raise ValueError(f"unclosed section tag(s) in template: {open_names}")
    return root


def _lookup(scopes: list[Any], path: str) -> Any:
    if path == ".":
        return scopes[-1]
    head, _, rest = path.partition(".")
    for scope in reversed(scopes):
        if isinstance(scope, dict) and head in scope:
            value = scope[head]
            for part in rest.split(".") if rest else []:
                value = value.get(part) if isinstance(value, dict) else None
            return value
    return None


def _render(nodes: list[_Node], scopes: list[Any]) -> str:
    out: list[str] = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(str(node[1]))
        elif kind == "var":
            value = _lookup(scopes, str(node[1]))
            out.append("" if value is None else str(value))
        elif kind == "section":
            name = str(node[1])
            children = node[2] or []
            value = _lookup(scopes, name)
            if isinstance(value, list):
                for item in value:
                    out.append(_render(children, [*scopes, item]))
            elif isinstance(value, dict):
                if value:
                    out.append(_render(children, [*scopes, value]))
            elif value:
                out.append(_render(children, scopes))
        elif kind == "inverted":
            name = str(node[1])
            children = node[2] or []
            value = _lookup(scopes, name)
            if not value:
                out.append(_render(children, scopes))
    return "".join(out)


def render(template: str, context: dict[str, Any]) -> str:
    """Render ``template`` against ``context``; pure function of its two arguments (no clock, no
    host, no filesystem state), so the same context always renders the same bytes."""
    return _render(_parse(template), [context])
