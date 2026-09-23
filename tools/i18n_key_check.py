"""Repository check: every message key referenced in engine code is defined in the vendored
catalogue, and every defined key is referenced from somewhere (SPEC §13.4 AX-6).

Scans the three modules that read from ``spec/i18n/messages.en.json`` at import or render time:
``error_catalogue.py`` (every key of its ``_KINDS`` registry implies a required
``errors.<key>.cause``/``errors.<key>.fix`` pair) and ``report.py``/``verdict.py`` (``report.``/
``verdict.``/``next.``/``outcome.`` keys, read either as a literal string subscript or as an
f-string with one trailing variable, e.g. ``cat[f"verdict.{state}"]``, which is treated as a
reference to every catalogue key sharing that literal prefix). ``tools/errors_check.py`` already
verifies a declared key's cause/fix are non-empty; this check is the complementary one: whether the
declared set and the referenced set agree at all.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG = _ROOT / "spec" / "i18n" / "messages.en.json"
_ERROR_CATALOGUE = _ROOT / "engines" / "python" / "agentce" / "error_catalogue.py"
_REPORT = _ROOT / "engines" / "python" / "agentce" / "report.py"
_VERDICT = _ROOT / "engines" / "python" / "agentce" / "verdict.py"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$")


def _kinds_dict_keys(source: str) -> set[str]:
    """Every string key of the module-level ``_KINDS`` dict literal (plain or type-annotated)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and target.id == "_KINDS"
            and isinstance(value, ast.Dict)
        ):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise ValueError("no module-level `_KINDS = {...}` dict literal found")


def _error_catalogue_refs(source: str) -> set[str]:
    refs: set[str] = set()
    for key in _kinds_dict_keys(source):
        refs.add(f"errors.{key}.cause")
        refs.add(f"errors.{key}.fix")
    return refs


#: Names the modules subscript to look a key up in the catalogue (report.py uses `cat`,
#: verdict.py uses `catalogue`); scoping to these avoids matching unrelated string literals
#: (filenames, Markdown/HTML/PDF template text) that merely happen to contain a dot.
_CATALOG_VAR_NAMES = frozenset({"cat", "catalogue", "catalog"})


def _key_expr(expr: ast.expr) -> tuple[str | None, str | None]:
    """(literal, prefix) for a key expression: a plain string constant, or an f-string whose
    literal prefix before the first interpolation is returned as a prefix reference."""
    if (
        isinstance(expr, ast.Constant)
        and isinstance(expr.value, str)
        and _KEY_RE.match(expr.value)
    ):
        return expr.value, None
    if isinstance(expr, ast.JoinedStr) and expr.values:
        first = expr.values[0]
        if (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and first.value
        ):
            return None, first.value
    return None, None


def _subscript_key_refs(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Literal and f-string-prefix keys read via `<catalog var>[...]` or `<catalog var>.get(...)`
    anywhere in the tree — the two lookup forms actually used in report.py/verdict.py."""
    literals: set[str] = set()
    prefixes: set[str] = set()

    def add(base: ast.expr, key_expr: ast.expr) -> None:
        base_name = (
            base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        )
        if base_name not in _CATALOG_VAR_NAMES:
            return
        literal, prefix = _key_expr(key_expr)
        if literal:
            literals.add(literal)
        if prefix:
            prefixes.add(prefix)

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            add(node.value, node.slice)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            add(node.func.value, node.args[0])
    return literals, prefixes


def _module_dict_literal_values(tree: ast.AST) -> set[str]:
    """Message-key-shaped string values of any module-level dict literal — covers an indirection
    table like report.py's `_SEVERITY_HEADING_KEY = {"high": "report.severity_high", ...}`, whose
    values (not its own keys) are the catalogue keys actually read, one level removed."""
    values: set[str] = set()
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Dict):
            for v in value.values:
                if (
                    isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                    and _KEY_RE.match(v.value)
                ):
                    values.add(v.value)
    return values


def _literal_and_prefix_refs(source: str) -> tuple[set[str], set[str]]:
    """Message keys a module reads from its catalogue variable: direct subscripts, f-string
    subscripts with one trailing variable, and values of a module-level key-indirection dict."""
    tree = ast.parse(source)
    literals, prefixes = _subscript_key_refs(tree)
    literals |= _module_dict_literal_values(tree)
    return literals, prefixes


def check(
    catalog_path: Path = _CATALOG,
    error_catalogue_path: Path = _ERROR_CATALOGUE,
    report_path: Path = _REPORT,
    verdict_path: Path = _VERDICT,
) -> list[str]:
    problems: list[str] = []
    defined: set[str] = set(json.loads(catalog_path.read_text(encoding="utf-8")).keys())

    referenced: set[str] = set(
        _error_catalogue_refs(error_catalogue_path.read_text(encoding="utf-8"))
    )
    prefixes: set[str] = set()
    for path in (report_path, verdict_path):
        literals, pfx = _literal_and_prefix_refs(path.read_text(encoding="utf-8"))
        referenced |= literals
        prefixes |= pfx

    covered = set(referenced)
    for key in defined:
        if any(key.startswith(p) for p in prefixes):
            covered.add(key)

    for key in sorted(referenced - defined):
        if any(key.startswith(p) for p in prefixes):
            continue  # a dynamic reference that happens to also appear as a dead literal elsewhere
        problems.append(f"referenced but undefined: {key}")

    for prefix in sorted(prefixes):
        if not any(k.startswith(prefix) for k in defined):
            problems.append(
                f"referenced key prefix matches no catalogue entry: {prefix}..."
            )

    for key in sorted(defined - covered):
        problems.append(f"defined but unused: {key}")

    return problems


def run_check() -> int:
    problems = check()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"I18N KEYS INCONSISTENT: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("I18N KEYS OK")
    return 0


def self_test() -> int:
    """Prove the checker discriminates: a good fixture set passes; a fixture with a referenced but
    undefined key, and a fixture with a defined but unused key, are each caught."""
    fixtures = Path(__file__).resolve().parent / "i18n" / "fixtures"
    failures: list[str] = []

    def run(name: str) -> list[str]:
        d = fixtures / name
        return check(
            catalog_path=d / "messages.en.json",
            error_catalogue_path=d / "error_catalogue.py",
            report_path=d / "report.py",
            verdict_path=d / "verdict.py",
        )

    good_problems = run("good")
    if good_problems:
        failures.append(f"good: expected no problems, got {good_problems}")

    for bad, expect in (
        ("bad-missing-key", "referenced but undefined"),
        ("bad-unused-key", "defined but unused"),
        ("bad-dead-prefix", "matches no catalogue entry"),
    ):
        problems = run(bad)
        if not problems:
            failures.append(f"{bad}: expected a problem, got none")
        elif not any(expect in p for p in problems):
            failures.append(f"{bad}: no problem contained {expect!r}; got {problems}")

    if failures:
        print("I18N KEY-CHECK SELF-TEST FAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(
        "I18N KEY-CHECK SELF-TEST PASSED — the checker catches a missing key, an unused key, and a dead dynamic prefix."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_check()


if __name__ == "__main__":
    raise SystemExit(main())
