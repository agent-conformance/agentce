"""A tiny, deterministic ``--fail-on`` expression language for ``agentce assess`` (SPEC §8.5, §7).

The grammar is comparisons of the form ``field=="literal"`` joined by ``and``/``or``, over a fixed
field allow-list. There is no ``eval``/``exec`` anywhere in this module, and no call, attribute-access,
or grouping syntax exists in the grammar at all: a hand-written tokenizer and a recursive-descent
parser only ever recognize a quoted string, the ``==`` operator, and a bare identifier, so an
injection payload (a dunder-import call, a backtick-embedded shell command, ``os.system(...)``, or a
syntactically valid-looking clause with an evaluable tail appended after ``or``) is refused as an
unexpected token at parse time, before any assertion is evaluated against the expression.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Union

from .assertions import Assertion
from .errors import InputError

#: The only fields a --fail-on expression may compare against (SPEC §9.4 assertion fields).
ALLOWED_FIELDS = frozenset(
    {"control", "subject", "outcome", "severity", "family", "rung", "mode"}
)

_KEYWORDS = frozenset({"and", "or"})

#: A predicate's evaluation tree: ("cmp", field, literal) | ("and", node, node) | ("or", node, node).
_Node = tuple[str, Union[str, "_Node"], Union[str, "_Node"]]


class _ExprError(Exception):
    """Internal syntax/semantic error; always translated to an ``InputError`` before it escapes."""


@dataclass(frozen=True)
class _Token:
    kind: str  # "string" | "op" | "ident"
    value: str


def _tokenize(expr: str) -> list[_Token]:
    """Lex ``expr`` into string/op/ident tokens. Any other character is a syntax error -- there is no
    catch-all "skip and continue" branch, so a backtick, a parenthesis, a dot, or any other character
    outside this tiny alphabet fails the whole expression rather than being silently discarded."""
    tokens: list[_Token] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c in ('"', "'"):
            quote = c
            j = i + 1
            buf: list[str] = []
            while j < n and expr[j] != quote:
                if expr[j] == "\\" and j + 1 < n:
                    buf.append(expr[j + 1])
                    j += 2
                    continue
                buf.append(expr[j])
                j += 1
            if j >= n:
                raise _ExprError(
                    f"unterminated string literal starting at position {i}"
                )
            tokens.append(_Token("string", "".join(buf)))
            i = j + 1
            continue
        if expr[i : i + 2] == "==":
            tokens.append(_Token("op", "=="))
            i += 2
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            tokens.append(_Token("ident", expr[i:j]))
            i = j
            continue
        raise _ExprError(f"unexpected character {c!r} at position {i}")
    return tokens


class _Parser:
    """expr := and_expr ("or" and_expr)* ; and_expr := comparison ("and" comparison)* ;
    comparison := FIELD "==" STRING. No parentheses, no other operators -- the whole grammar."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> _Token | None:
        tok = self._peek()
        self._pos += 1
        return tok

    def parse(self) -> _Node:
        node = self._or_expr()
        trailing = self._peek()
        if trailing is not None:
            raise _ExprError(
                f"unexpected token {trailing.value!r} after a complete expression"
            )
        return node

    def _or_expr(self) -> _Node:
        left = self._and_expr()
        while True:
            tok = self._peek()
            if tok is not None and tok.kind == "ident" and tok.value == "or":
                self._advance()
                left = ("or", left, self._and_expr())
            else:
                return left

    def _and_expr(self) -> _Node:
        left = self._comparison()
        while True:
            tok = self._peek()
            if tok is not None and tok.kind == "ident" and tok.value == "and":
                self._advance()
                left = ("and", left, self._comparison())
            else:
                return left

    def _comparison(self) -> _Node:
        field_tok = self._advance()
        if field_tok is None or field_tok.kind != "ident":
            raise _ExprError("expected a field name")
        if field_tok.value in _KEYWORDS:
            raise _ExprError(
                f"expected a field name, got the reserved word {field_tok.value!r}"
            )
        if field_tok.value not in ALLOWED_FIELDS:
            raise _ExprError(
                f"unknown field {field_tok.value!r}; choose from: "
                + ", ".join(sorted(ALLOWED_FIELDS))
            )
        op_tok = self._advance()
        if op_tok is None or op_tok.kind != "op":
            raise _ExprError(f"expected '==' after field name {field_tok.value!r}")
        val_tok = self._advance()
        if val_tok is None or val_tok.kind != "string":
            raise _ExprError("expected a quoted string literal after '=='")
        return ("cmp", field_tok.value, val_tok.value)


_FIELD_GETTERS: dict[str, Callable[[Assertion], str]] = {
    "control": lambda a: a.control,
    "subject": lambda a: a.subject,
    "outcome": lambda a: a.outcome,
    "severity": lambda a: a.severity,
    "family": lambda a: a.family,
    "rung": lambda a: str(a.rung),
    "mode": lambda a: a.mode,
}


def _eval_node(node: _Node, assertion: Assertion) -> bool:
    kind = node[0]
    if kind == "cmp":
        field, literal = node[1], node[2]
        assert isinstance(field, str) and isinstance(
            literal, str
        )  # narrows for mypy; always true
        return _FIELD_GETTERS[field](assertion) == literal
    if kind == "and":
        left, right = node[1], node[2]
        assert isinstance(left, tuple) and isinstance(right, tuple)
        return _eval_node(left, assertion) and _eval_node(right, assertion)
    if kind == "or":
        left, right = node[1], node[2]
        assert isinstance(left, tuple) and isinstance(right, tuple)
        return _eval_node(left, assertion) or _eval_node(right, assertion)
    raise AssertionError(f"unreachable: unknown node kind {kind!r}")  # pragma: no cover


def parse_fail_on(expr: str) -> Callable[[Assertion], bool]:
    """Parse a ``--fail-on`` expression into a predicate over an :class:`Assertion`.

    Raises :class:`InputError` with a stable ``input.fail_on_invalid_expression`` key on any syntax
    error, unknown field, or disallowed token — including every attempted code-injection shape (a
    dunder-import call, a backtick-embedded shell command, a bare function call, or a well-formed
    clause with an evaluable tail appended after ``or``), because the grammar has no rule that could
    ever accept a call, an attribute access, or a bare unquoted identifier as a value. Refusal happens
    here, at parse time, before any assertion is evaluated against the expression (SPEC §7: never
    ``eval``).
    """
    try:
        tokens = _tokenize(expr)
        if not tokens:
            raise _ExprError("the --fail-on expression is empty")
        node = _Parser(tokens).parse()
    except _ExprError as exc:
        raise InputError(
            "input.fail_on_invalid_expression",
            f"--fail-on {expr!r} is not a valid expression: {exc}",
            'use comparisons of the form field=="literal" joined by and/or, over: '
            + ", ".join(sorted(ALLOWED_FIELDS))
            + ".",
        ) from exc

    def predicate(assertion: Assertion) -> bool:
        return _eval_node(node, assertion)

    return predicate
