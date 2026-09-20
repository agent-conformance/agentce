"""Repository check: the engine container image is built from the wheel and its context is clean.

The image is built with ``docker build -f engines/python/Dockerfile .``, so the build context is the
repository root and Docker filters it with the ``.dockerignore`` there. Two facts must hold:

* the ``.dockerignore`` keeps private and dev paths (``SPECS``, ``.git``, ``node_modules``, ``dist``,
  ``tests``) out of the context, both as directories and as any path beneath them, applying the rules
  the way Docker does (the last matching rule wins, so a ``!`` re-inclusion counts);
* the Dockerfile is multi-stage, its final stage installs a wheel built by an earlier stage, and that
  stage neither copies the whole repository nor runs the engine from a source tree.

Usage:
    container_recipe_check.py              check the repository (exit 1 on any violation)
    container_recipe_check.py --self-test  prove the check discriminates on planted bad recipes
"""

from __future__ import annotations

import fnmatch
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = Path("engines") / "python" / "Dockerfile"
KEEP_OUT = ("SPECS", ".git", "node_modules", "dist", "tests")


def _rules(text: str) -> list[tuple[bool, str]]:
    rules: list[tuple[bool, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        line = line.lstrip("/").rstrip("/").removeprefix("**/")
        if line:
            rules.append((negated, line))
    return rules


def _excluded(rules: list[tuple[bool, str]], path: str) -> bool:
    parts = path.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    result = False
    for negated, pattern in rules:
        if any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes):
            result = not negated
    return result


def dockerignore_violations(root: Path) -> list[str]:
    path = root / ".dockerignore"
    if not path.is_file():
        return [".dockerignore: missing at the build-context root"]
    rules = _rules(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for name in KEEP_OUT:
        if not (_excluded(rules, name) and _excluded(rules, f"{name}/probe.txt")):
            problems.append(f".dockerignore: does not exclude {name}")
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
    final = lines[starts[-1] :]
    final_text = "\n".join(final)
    shell_form = re.sub(
        r'[\[\]",]', " ", final_text
    )  # exec-form JSON arrays read as a command line
    if ".whl" not in "\n".join(lines):
        problems.append("Dockerfile: no instruction references a wheel (.whl)")
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


GOOD_IGNORE = "SPECS\n.git\nnode_modules\ndist\ntests\n"
GOOD_DOCKERFILE = (
    "FROM python:3.12-slim AS builder\n"
    "COPY engines/python/ ./\n"
    "RUN uv build --wheel --out-dir /dist\n"
    "FROM python:3.12-slim\n"
    "COPY --from=builder /dist/*.whl /tmp/wheel/\n"
    "RUN pip install --no-deps /tmp/wheel/*.whl\n"
    'ENTRYPOINT ["agentce"]\n'
)


def _tree(ignore: str | None, dockerfile: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / DOCKERFILE).parent.mkdir(parents=True)
        (root / DOCKERFILE).write_text(dockerfile, encoding="utf-8")
        if ignore is not None:
            (root / ".dockerignore").write_text(ignore, encoding="utf-8")
        (root / "SPECS").mkdir()
        return violations(root)


def self_test() -> int:
    cases: dict[str, tuple[str | None, str, bool]] = {
        "a clean recipe": (GOOD_IGNORE, GOOD_DOCKERFILE, True),
        "no .dockerignore": (None, GOOD_DOCKERFILE, False),
        "a near-miss pattern": (
            GOOD_IGNORE.replace("SPECS", "SPEC"),
            GOOD_DOCKERFILE,
            False,
        ),
        "a re-included directory": (GOOD_IGNORE + "!tests\n", GOOD_DOCKERFILE, False),
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
