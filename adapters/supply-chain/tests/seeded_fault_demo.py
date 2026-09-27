"""Seeded-fault demo for the signature gate: break the verifier in a temporary copy and watch the tests go RED.

Fault ``a`` makes the signature check always succeed; fault ``b`` drops the observed-digest comparison. Each
must turn the signature tests and the hostile fixture RED; the unmodified copy must be GREEN. A token is
printed only from the matching subprocess exit code.

    uv run python tests/seeded_fault_demo.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = Path("src/agentce_adapters/supply_chain.py")

FAULTS = {
    "a": (
        "        if isinstance(key, ed25519.Ed25519PublicKey):\n            key.verify(signature, message)\n",
        "        return True\n        if isinstance(key, ed25519.Ed25519PublicKey):\n            key.verify(signature, message)\n",
    ),
    "b": (
        "    elif observed != set(claims.subject_digests):",
        "    elif False:",
    ),
}


def run_tests(tree: Path) -> int:
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    probe = subprocess.run(
        [sys.executable, "-c", "import agentce_adapters as a; print(a.__file__)"],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    if not probe.stdout.startswith(str(tree)):
        raise SystemExit(
            f"the copy under test is not the one imported: {probe.stdout.strip()}"
        )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-x",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "tests/test_signatures.py",
            "tests/test_adapter.py",
        ],
        cwd=tree,
        env=env,
        capture_output=True,
    ).returncode


def main() -> int:
    tokens: list[str] = []
    for name, (old, new) in FAULTS.items():
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            for part in ("src", "tests", "fixtures"):
                shutil.copytree(
                    ROOT / part,
                    tree / part,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
            source = (tree / MODULE).read_text(encoding="utf-8")
            if source.count(old) != 1:
                raise SystemExit(f"fault {name}: the code it targets has moved")
            (tree / MODULE).write_text(source.replace(old, new), encoding="utf-8")
            if run_tests(tree) != 0:
                tokens.append(f"RED_ON_FAULT {name}")
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        for part in ("src", "tests", "fixtures"):
            shutil.copytree(
                ROOT / part, tree / part, ignore=shutil.ignore_patterns("__pycache__")
            )
        if run_tests(tree) == 0:
            tokens.append("GREEN_AFTER_RESTORE")
    print(" ".join(tokens))
    return 0 if len(tokens) == len(FAULTS) + 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
