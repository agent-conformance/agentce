"""Repository check: the engine container image is built from the wheel and its context is clean.

The image is built with ``docker build -f engines/python/Dockerfile .``, so the build context is the
repository root and Docker filters it with the ``.dockerignore`` there. Two facts must hold:

* the ``.dockerignore`` lets only the Python engine's sources into the context. It denies by default,
  so it is checked by behaviour rather than by a list of names: every top-level entry actually present
  in the repository other than the engines directory is excluded, every engine other than the Python
  one is excluded, the engine's own tests, caches, and build output are excluded, a path no rule
  mentions is excluded, and the files the wheel build needs are still included. Rules are applied the
  way Docker applies them: a rule matching a path or any parent decides, and the last matching rule
  wins, so a ``!`` re-inclusion counts;
* the Dockerfile is multi-stage, its final stage installs a wheel that an earlier stage built and it
  copies in by stage name, and that stage neither copies the whole build context nor runs the engine
  from a source tree.

Usage:
    container_recipe_check.py              check the repository (exit 1 on any violation)
    container_recipe_check.py --self-test  prove the check discriminates on planted bad recipes
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = Path("engines") / "python" / "Dockerfile"
ENGINE = "engines/python"
# What the wheel build needs, and what must never reach the context (paths no tracked file has to exist for).
NEEDED = (
    f"{ENGINE}/pyproject.toml",
    f"{ENGINE}/uv.lock",
    f"{ENGINE}/README.md",
    f"{ENGINE}/agentce/cli.py",
)
KEPT_OUT = (
    ".git/config",
    "node_modules/pkg/index.js",
    "dist/pkg.whl",
    "tests/test_x.py",
    "an-unlisted-top-level-directory/file.txt",
    "engines/typescript/package.json",
    "engines/java/build.gradle.kts",
    f"{ENGINE}/tests/test_x.py",
    f"{ENGINE}/dist/pkg.whl",
    f"{ENGINE}/.venv/bin/python",
    f"{ENGINE}/agentce/__pycache__/cli.pyc",
)


def _regex(pattern: str) -> re.Pattern[str]:
    """A .dockerignore pattern as a regular expression over a slash-separated relative path."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out)


def _rules(text: str) -> list[tuple[bool, re.Pattern[str]]]:
    rules: list[tuple[bool, re.Pattern[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        line = line.lstrip("/").rstrip("/")
        if line:
            rules.append((negated, _regex(line)))
    return rules


def _excluded(rules: list[tuple[bool, re.Pattern[str]]], path: str) -> bool:
    parts = path.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    result = False
    for negated, pattern in rules:
        if any(pattern.fullmatch(prefix) for prefix in prefixes):
            result = not negated
    return result


def dockerignore_violations(root: Path) -> list[str]:
    path = root / ".dockerignore"
    if not path.is_file():
        return [".dockerignore: missing at the build-context root"]
    rules = _rules(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    present = [entry.name for entry in root.iterdir() if entry.name != "engines"]
    present += [
        f"engines/{entry.name}"
        for entry in (root / "engines").iterdir()
        if entry.name != "python"
    ]
    for probe in [*KEPT_OUT, *present]:
        if not _excluded(rules, probe):
            problems.append(f".dockerignore: {probe} would enter the build context")
    for needed in NEEDED:
        if _excluded(rules, needed):
            problems.append(
                f".dockerignore: {needed} is excluded but the wheel build needs it"
            )
    return problems


def _instructions(text: str) -> list[str]:
    """Instruction lines with continuations joined and comments dropped."""
    lines: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        buffer = f"{buffer} {stripped}" if buffer else stripped
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        if buffer:
            lines.append(buffer)
        buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


BUILDS_A_WHEEL = re.compile(
    r"\b(uv\s+build|pip\d*\s+wheel|python\d*\s+-m\s+build|hatch\s+build)\b", re.I
)


def dockerfile_violations(text: str) -> list[str]:
    lines = _instructions(text)
    starts = [i for i, line in enumerate(lines) if re.match(r"FROM\s", line, re.I)]
    if not starts:
        return ["Dockerfile: no FROM instruction"]
    problems: list[str] = []
    if len(starts) < 2:
        problems.append(
            "Dockerfile: not multi-stage (the image must be built from a wheel, not a tree)"
        )
    stages: dict[str, list[str]] = {}
    for n, start in enumerate(starts[:-1]):
        name = re.match(r"FROM\s+\S+\s+AS\s+(\S+)", lines[start], re.I)
        if name:
            stages[name.group(1).lower()] = lines[start : starts[n + 1]]
    final = lines[starts[-1] :]
    final_text = "\n".join(final)
    shell_form = re.sub(
        r'[\[\]",]', " ", final_text
    )  # exec-form JSON arrays read as a command line
    copied = [
        m.group(1).lower()
        for line in final
        if (m := re.match(r"COPY\s+--from=(\S+)\s+\S*\.whl\b", line, re.I))
    ]
    if not any(
        BUILDS_A_WHEEL.search("\n".join(stages.get(stage, []))) for stage in copied
    ):
        problems.append(
            "Dockerfile: the final stage does not copy in a wheel that an earlier stage builds"
        )
    if not re.search(r"pip\s+install\b.*\.whl", final_text, re.I):
        problems.append("Dockerfile: the final stage does not install a wheel")
    if any(
        re.match(r"(COPY|ADD)\s+(--\S+\s+)*\.\s+\S+\s*$", line, re.I) for line in final
    ):
        problems.append("Dockerfile: the final stage copies the whole build context")
    if re.search(r"uv\s+(run|sync)\b.*--project", shell_form, re.I):
        problems.append(
            "Dockerfile: the final stage runs the engine from a source tree"
        )
    return problems


def violations(root: Path) -> list[str]:
    dockerfile = root / DOCKERFILE
    if not dockerfile.is_file():
        return [f"{DOCKERFILE}: missing"]
    return dockerignore_violations(root) + dockerfile_violations(
        dockerfile.read_text(encoding="utf-8")
    )


GOOD_IGNORE = "*\n!engines\nengines/*\n!engines/python\n**/tests\n**/dist\n**/.venv\n**/__pycache__\n**/node_modules\n**/.git\n"
GOOD_DOCKERFILE = (
    "FROM python:3.12-slim AS builder\n"
    "COPY engines/python/ ./\n"
    "RUN uv build --wheel --out-dir /dist\n"
    "FROM python:3.12-slim\n"
    "COPY --from=builder /dist/*.whl /tmp/wheel/\n"
    "RUN pip install --no-deps /tmp/wheel/*.whl\n"
    'ENTRYPOINT ["agentce"]\n'
)
NEEDED_FILES = (*NEEDED, f"{ENGINE}/agentce/__init__.py")


def _tree(ignore: str | None, dockerfile: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in (
            *NEEDED_FILES,
            "engines/typescript/package.json",
            "docs/index.md",
            "private-notes/plan.md",
        ):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("x\n", encoding="utf-8")
        (root / DOCKERFILE).write_text(dockerfile, encoding="utf-8")
        if ignore is not None:
            (root / ".dockerignore").write_text(ignore, encoding="utf-8")
        return violations(root)


def self_test() -> int:
    cases: dict[str, tuple[str | None, str, bool]] = {
        "a clean recipe": (GOOD_IGNORE, GOOD_DOCKERFILE, True),
        "no .dockerignore": (None, GOOD_DOCKERFILE, False),
        "an exclusion list that names paths but denies nothing by default": (
            ".git\nnode_modules\ndist\ntests\n",
            GOOD_DOCKERFILE,
            False,
        ),
        "a re-included tests directory": (
            GOOD_IGNORE + "!engines/python/tests\n",
            GOOD_DOCKERFILE,
            False,
        ),
        "a re-included other engine": (
            GOOD_IGNORE + "!engines/typescript\n",
            GOOD_DOCKERFILE,
            False,
        ),
        "a rule that starves the wheel build": (
            GOOD_IGNORE + "engines/python/agentce\n",
            GOOD_DOCKERFILE,
            False,
        ),
        "a single-stage tree copy": (
            GOOD_IGNORE,
            "FROM python:3.12-slim\nCOPY . .\nRUN uv sync --frozen --project engines/python\n",
            False,
        ),
        "a runtime stage that copies the repo": (
            GOOD_IGNORE,
            GOOD_DOCKERFILE + "COPY . /app\n",
            False,
        ),
        "a runtime stage that runs from source": (
            GOOD_IGNORE,
            GOOD_DOCKERFILE
            + 'CMD ["uv", "run", "--project", "engines/python", "agentce"]\n',
            False,
        ),
        "a wheel mentioned only in a comment": (
            GOOD_IGNORE,
            "# built from a .whl\nFROM python:3.12-slim AS a\nFROM python:3.12-slim\nCOPY --from=a /x /x\n",
            False,
        ),
        "a wheel that no stage builds": (
            GOOD_IGNORE,
            "FROM python:3.12-slim AS a\nFROM python:3.12-slim\nRUN pip install /nope/fake.whl\n",
            False,
        ),
        "a wheel copied from a stage that builds none": (
            GOOD_IGNORE,
            GOOD_DOCKERFILE.replace("uv build --wheel --out-dir /dist", "echo hello"),
            False,
        ),
    }
    failures = 0
    for name, (ignore, dockerfile, should_pass) in cases.items():
        found = _tree(ignore, dockerfile)
        if bool(found) == should_pass:
            print(
                f"SELF-TEST FAILED: {name}: expected {'pass' if should_pass else 'a violation'}, got {found}"
            )
            failures += 1
    if failures:
        return 1
    print(f"container_recipe_check self-test ok ({len(cases)} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()
    if args:
        print(__doc__)
        return 2
    problems = violations(ROOT)
    if problems:
        print("CONTAINER RECIPE FAILED:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("container recipe ok: wheel-based multi-stage build with a clean context")
    return 0


if __name__ == "__main__":
    sys.exit(main())
