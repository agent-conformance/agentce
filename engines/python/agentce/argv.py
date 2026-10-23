"""The command-line grammar scanner (SPEC §8.5, "Command-line grammar").

Every engine reads argv under one table, ``spec/cli/argv-grammar.json`` (vendored byte for byte to
``agentce/data/cli/``), with the same one-pass scan: no prefix matching, no short-flag clusters, no
option given twice unless the table marks it repeatable, a value only after ``=`` or in the next token
when that token does not start with ``-``, and ``--`` ending the options. ``-h``/``--help`` in an option
position wins over any refusal; otherwise the first refusal in argv order is raised, keyed by the
command. The scan never reads the file system. Mirrors ``engines/typescript/src/argv.ts`` and
``engines/java/.../Argv.java``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from . import bundled
from .errors import InputError

HELP = ("-h", "--help")


class ArgvRefusal(InputError):
    """A line outside the grammar: exit 3 under the command's key, naming the SPEC 8.5 rule."""

    def __init__(self, rule: str, token: str, key: str, cause: str, fix: str) -> None:
        super().__init__(key, cause, fix)
        self.rule = rule
        self.token = token


@dataclass(frozen=True)
class Option:
    names: tuple[str, ...]
    kind: str
    repeatable: bool
    needs_value_key: str | None = None
    value_hint: str = "<value>"
    choices: tuple[str, ...] | None = None

    @property
    def name(self) -> str:
        return self.names[-1]


@dataclass(frozen=True)
class Level:
    """One command, or one action under it: its options and its positional bound."""

    options: Mapping[str, Option]
    max_positionals: int | None
    actions: Mapping[str, Level] = field(default_factory=dict)


@dataclass(frozen=True)
class Command:
    name: str
    level: Level
    unrecognized_key: str
    needs_value_key: str
    flag_fix: str
    extra_fix: str
    implemented_in: tuple[str, ...]


@dataclass
class Scan:
    """What a scan read. ``help`` is set when the line asks for a usage; ``action`` then names whose
    (``None``: the command's). ``values`` holds single values, ``lists`` a repeatable option's values in
    argv order, ``flags`` the flags given, each by the option's last spelling."""

    help: bool = False
    action: str | None = None
    values: dict[str, str] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    positionals: list[str] = field(default_factory=list)


def _option(raw: Mapping[str, Any]) -> Option:
    choices = raw.get("choices")
    return Option(
        names=tuple(raw["names"]),
        kind=raw["kind"],
        repeatable=raw["repeatable"],
        needs_value_key=raw.get("needs_value_key"),
        value_hint=raw.get("value_hint", "<value>"),
        choices=None if choices is None else tuple(choices),
    )


def _level(raw: Mapping[str, Any], globals_: Sequence[Option]) -> Level:
    options = {
        n: o for o in (*globals_, *map(_option, raw["options"])) for n in o.names
    }
    actions = {
        name: _level(action, globals_)
        for name, action in raw.get("actions", {}).items()
    }
    return Level(options, raw["positionals"]["max"], actions)


@cache
def grammar() -> Mapping[str, Any]:
    """The vendored grammar file, as data."""
    with (bundled._data_root() / "cli" / "argv-grammar.json").open(
        encoding="utf-8"
    ) as fh:
        loaded: Mapping[str, Any] = json.load(fh)
    return loaded


@cache
def globals_() -> tuple[Option, ...]:
    return tuple(_option(o) for o in grammar()["globals"])


@cache
def command(name: str) -> Command:
    raw = grammar()["commands"][name]
    return Command(
        name=name,
        level=_level(raw, globals_()),
        unrecognized_key=raw["keys"]["unrecognized"],
        needs_value_key=raw["keys"]["needs_value"],
        flag_fix=raw["fixes"]["flag"],
        extra_fix=raw["fixes"]["extra"],
        implemented_in=tuple(raw["implemented_in"]),
    )


ChoiceError = Callable[[str, str], InputError]
ActionError = Callable[[], InputError]


def scan(
    cmd: Command,
    tokens: Sequence[str],
    *,
    seen: Sequence[str] = (),
    choice_error: ChoiceError | None = None,
    action_error: ActionError | None = None,
) -> Scan:
    """Read ``tokens`` (the line after the command word) under ``cmd``'s grammar. ``seen`` holds the
    global flags already given before the command word (a second one is a repeat). ``choice_error``
    words a value outside an option's choices (given the option's name and the value);
    ``action_error`` words an action the command does not have."""
    out = Scan()
    level = cmd.level
    given: set[str] = set(seen)
    first: InputError | None = None
    help_seen = False
    help_action: str | None = None

    def refuse(
        rule: str, token: str, cause: str, fix: str, key: str | None = None
    ) -> None:
        nonlocal first
        if first is None:
            first = ArgvRefusal(rule, token, key or cmd.unrecognized_key, cause, fix)

    def unrecognized(rule: str, token: str) -> None:
        refuse(rule, token, f"unrecognized flag '{token}'.", cmd.flag_fix)

    ended = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if ended:
            _positional(out, level, cmd, token, refuse)
            continue
        if token == "--":
            if level.actions and out.action is None:
                _refuse_action(refuse, action_error, token)
                break
            if level.max_positionals == 0 or i == len(tokens):
                refuse(
                    "double_dash_alone", token, "unrecognized flag '--'.", cmd.flag_fix
                )
            ended = True
            continue
        if token in HELP:
            if not help_seen:
                help_seen, help_action = True, out.action
            continue
        if not token.startswith("-"):
            if level.actions and out.action is None:
                if token in level.actions:
                    out.action = token
                    level = level.actions[token]
                    continue
                _refuse_action(refuse, action_error, token)
                break
            _positional(out, level, cmd, token, refuse)
            continue
        name, eq, rest = token.partition("=")
        opt = level.options.get(name)
        if opt is None:
            if name in HELP:
                refuse(
                    "value_on_flag",
                    token,
                    f"flag '{name}' takes no value.",
                    f"drop the value: {name}.",
                )
            elif token.startswith("--"):
                unrecognized("exact_spelling", token)
            elif len(token) > 2:
                unrecognized("short_cluster", token)
            else:
                unrecognized("dash_token_not_option", token)
            continue
        if opt.name in given and not opt.repeatable:
            refuse(
                "repeated_option",
                token,
                f"flag '{name}' was given more than once.",
                f"give {name} once.",
            )
        given.add(opt.name)
        if opt.kind == "flag":
            if eq:
                refuse(
                    "value_on_flag",
                    token,
                    f"flag '{name}' takes no value.",
                    f"drop the value: {name}.",
                )
            out.flags.add(opt.name)
            continue
        if eq:
            value = rest
        elif i < len(tokens) and not tokens[i].startswith("-"):
            value = tokens[i]
            i += 1
        else:
            nxt = tokens[i] if i < len(tokens) else None
            rule = (
                "needs_value"
                if nxt is None
                else "needs_value_before_double_dash"
                if nxt == "--"
                else "needs_value_before_option"
            )
            refuse(
                rule,
                name,
                f"flag '{name}' needs a value.",
                f"pass {name} {opt.value_hint}.",
                opt.needs_value_key or cmd.needs_value_key,
            )
            if nxt in HELP:
                i += 1  # help in a value slot is no help (SPEC 8.5)
            continue
        if (
            opt.choices is not None
            and value not in opt.choices
            and choice_error is not None
        ):
            err = choice_error(opt.name, value)
            refuse("choices", token, err.cause, err.fix, err.key)
        if opt.repeatable:
            out.lists.setdefault(opt.name, []).append(value)
        else:
            out.values[opt.name] = value
    if help_seen:
        return Scan(help=True, action=help_action)
    if first is not None:
        raise first
    return out


def _positional(
    out: Scan, level: Level, cmd: Command, token: str, refuse: Callable[..., None]
) -> None:
    if (
        level.max_positionals is not None
        and len(out.positionals) >= level.max_positionals
    ):
        refuse(
            "extra_positional",
            token,
            f"unrecognized argument '{token}'.",
            cmd.extra_fix,
        )
    else:
        out.positionals.append(token)


def _refuse_action(
    refuse: Callable[..., None], action_error: ActionError | None, token: str
) -> None:
    assert action_error is not None, "a command with actions words its unknown action"
    err = action_error()
    refuse("unknown_action", token, err.cause, err.fix, err.key)
