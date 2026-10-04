"""VG-UX-NEXT-STEP (18.40, USER_EXPERIENCE.md R4): every message key the Python engine raises with a
literal key and a literal fix string is registered in the catalogue with a non-empty fix, and -- when
every one of its literal-fix call sites agrees on the exact text -- registered with that exact text
(so the generated reference never drifts from what the code actually says). A fixed set of 10 keys
whose fix needs an actor other than the person at the terminal (R4 part 2) must name that actor.

Scope (decision recorded in harness/decisions/phase-18.tsv, round 2 of 18.40's contract-critic): this
scan only considers an ``InputError(...)``/``AgentceError(...)`` call site whose ``key`` argument is a
literal string. A call reached only through a shared forwarder that composes its own key at runtime
(``_require_dir``, ``_require_file``, ``load_untrusted_yaml``, ...) has a non-literal ``key`` at the
call site and is out of scope here -- tracked by item 18.40f, not this one. Within a literal-key call,
only a literal-string ``fix`` argument counts as a "voice"; a key whose only fix text is built with an
f-string, a variable reference, or a concatenation (``input.collect_config``, ``input.emit_format``,
``input.report_format``, ``input.fail_on_invalid_expression``) is also out of scope here for the same
reason -- the gate cannot know what text a non-literal expression will produce without running it, and
is not asked to.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCAN_ROOT = ROOT / "engines" / "python" / "agentce"
DEFAULT_CATALOGUE = ROOT / "spec" / "i18n" / "messages.en.json"

#: The exception constructors this gate follows. Both take (key, cause, fix, ...) positionally, and
#: AgentceError's dataclass fields let callers also use the keyword form.
_RAISERS = ("InputError", "AgentceError")

#: R4 part 2: these keys' fix must name an actor other than the person running the command.
#: ``unknown_source`` is deliberately excluded -- its actor is already the profile's author, named
#: directly ("declare the source..."), so no rewrite was needed for it.
_ACTOR_KEYS = frozenset(
    {
        "class_mismatch",
        "context_mismatch",
        "duplicate_id",
        "oversize",
        "schema_invalid",
        "time_order",
        "unknown_type",
        "internal.unexpected",
        "input.records_no_genai_spans",
        "insufficient_evidence",
    }
)

#: A fix text names an actor other than the terminal user if it matches one of these markers. "ask" is
#: a whole-word match (a bare substring also matches inside "task", round-2 critic finding).
_ACTOR_MARKERS = (re.compile(r"\bask\b"), re.compile(r"file an issue for"))

SUCCESS_LINE = "UX-NEXT-STEP OK"


def _literal_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_arg(call: ast.Call, position: int, name: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _scan_file(
    path: Path, voices: dict[str, list[str]], has_other_voice: set[str]
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _RAISERS:
            key = _literal_str(_call_arg(node, 0, "key"))
            if key is None:
                continue  # key composed at runtime by a shared forwarder -- out of scope (18.40f)
            fix = _literal_str(_call_arg(node, 2, "fix"))
            if fix is None:
                # a real call site for this key whose text the gate cannot read statically (an
                # f-string, a variable, a concatenation) -- proof this key has a second real voice
                # beyond whatever literal text other call sites share, so it is not single-voice.
                has_other_voice.add(key)
                continue
            voices.setdefault(key, []).append(fix)
        elif isinstance(func, ast.Name) and func.id == "load_untrusted_yaml":
            # always renders its own, non-literal, exception-specific text for the caller's key --
            # a genuine second voice distinct from whatever the direct call sites say (finding 5).
            key = _literal_str(_call_arg(node, 2, "key"))
            if key is not None:
                has_other_voice.add(key)
        elif isinstance(func, ast.Name) and func.id == "_doctor_problem":
            # only counts as a second voice when this call passes its own fix override -- a bare
            # ``_doctor_problem(key, problem)`` just echoes the catalogue and is not a second voice.
            key = _literal_str(_call_arg(node, 0, "key"))
            if key is not None and _call_arg(node, 2, "fix") is not None:
                has_other_voice.add(key)


def scan(scan_root: Path) -> tuple[dict[str, list[str]], set[str]]:
    """key -> literal fix texts from its literal-key, literal-fix call sites, plus the set of keys
    that also have a real, non-literal second voice elsewhere (so byte-match does not apply)."""
    voices: dict[str, list[str]] = {}
    has_other_voice: set[str] = set()
    for path in sorted(scan_root.rglob("*.py")):
        _scan_file(path, voices, has_other_voice)
    return voices, has_other_voice


def check(
    voices: dict[str, list[str]], has_other_voice: set[str], catalogue: dict[str, str]
) -> list[str]:
    problems: list[str] = []
    for key in sorted(voices):
        distinct = sorted(set(voices[key]))
        multi_voice = len(distinct) > 1 or key in has_other_voice
        registered = catalogue.get(f"errors.{key}.fix")
        if not registered:
            voice = "multi-voice" if multi_voice else "single-voice"
            problems.append(
                f"{key}: never registered ({voice}; code says {distinct!r})"
            )
            continue
        if not multi_voice and registered != distinct[0]:
            problems.append(
                f"{key}: registered fix text differs from its one real call site "
                f"(registered {registered!r}, code says {distinct[0]!r})"
            )
    for key in sorted(_ACTOR_KEYS):
        registered = catalogue.get(f"errors.{key}.fix", "")
        if not any(marker.search(registered) for marker in _ACTOR_MARKERS):
            problems.append(
                f"{key}: fix does not name an actor other than the person at the terminal"
            )
    return problems


def _load_catalogue(path: Path) -> dict[str, str]:
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-root", type=Path, default=DEFAULT_SCAN_ROOT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument(
        "--list-raised-keys",
        action="store_true",
        help="print every key this scan finds raised with a literal key and fix, one per line, and exit 0",
    )
    args = parser.parse_args(argv)

    voices, has_other_voice = scan(args.scan_root)

    if args.list_raised_keys:
        for key in sorted(voices):
            print(key)
        return 0

    catalogue = _load_catalogue(args.catalog)
    problems = check(voices, has_other_voice, catalogue)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"UX-NEXT-STEP: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(SUCCESS_LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
