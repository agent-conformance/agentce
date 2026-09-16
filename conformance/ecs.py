"""Two-engine Engine Conformance Suite runner and comparator (SPEC §8.2 #9, §11.5, P3.4).

Runs the same corpus through more than one engine and compares their outputs. The engines assess a
single materialised full corpus (so the inputs are identical to the byte), and the comparison is over
each project's ``assertions.json`` — the canonical source of truth that must be byte-identical across
conforming engines after RFC 8785 canonicalisation (a translation such as OSCAL or SARIF never changes
it). Run it as::

    cd conformance && uv run python ecs.py --engines python,typescript --corpus ../corpus --compare

``--compare`` exits 0 only when every engine claims ``full`` and every project's assertions are
identical across engines; ``--json`` prints ``{"python": …, "ts": …, "projects_total": …,
"projects_identical": …, …}``. The corpus source tree is materialised as the full set on demand, so the
corpus output need never be committed (SPEC §11.7).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# The corpus generator lives in the corpus package; make it importable from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_ENGINE = REPO_ROOT / "engines" / "python"
TS_ENGINE = REPO_ROOT / "engines" / "typescript"


def _materialise_full(corpus_dir: Path) -> Path:
    """Return a directory holding the full-set ``corpus-manifest.json``, generating it if needed."""
    manifest = corpus_dir / "corpus-manifest.json"
    if (
        manifest.is_file()
        and json.loads(manifest.read_text(encoding="utf-8")).get("set") == "full"
    ):
        return corpus_dir
    if (corpus_dir / "generator" / "generate.py").is_file():
        from corpus.generator.generate import build_corpus

        out = Path(tempfile.mkdtemp(prefix="ecs-corpus-"))
        build_corpus(out, "full")
        return out
    raise SystemExit(
        f"{corpus_dir} has neither a full corpus-manifest.json nor a generator."
    )


def _run_python(corpus_root: Path, out_dir: Path) -> str:
    """Run the Python reference engine's ECS over ``corpus_root``; return its claim."""
    from agentce.conformance import run_ecs

    report = run_ecs(engine_path=PY_ENGINE, corpus_dir=corpus_root, out_dir=out_dir)
    return str(report["claim"])


def _run_typescript(corpus_root: Path, out_dir: Path) -> str:
    """Run the TypeScript engine's ECS over ``corpus_root`` (via pnpm); return its claim."""
    proc = subprocess.run(
        [
            "pnpm",
            "--silent",
            "agentce",
            "conformance",
            "run",
            "--engine",
            ".",
            "--corpus",
            str(corpus_root.resolve()),
            "--out",
            str(out_dir.resolve()),
            "--json",
        ],
        cwd=str(TS_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = proc.stdout
    start = stdout.find("{")
    if start < 0:
        raise SystemExit(
            f"the TypeScript engine produced no JSON envelope:\n{(proc.stderr or stdout)[-800:]}"
        )
    envelope = json.loads(stdout[start:])
    return str(envelope["claim"])


def _assertions_bytes(out_dir: Path, project_id: str) -> bytes | None:
    path = out_dir / "projects" / project_id / "assertions.json"
    return path.read_bytes() if path.is_file() else None


def run_two_engine(corpus_dir: Path) -> dict[str, Any]:
    """Run both engines over the full corpus and compare their assertions byte for byte (P3.4)."""
    corpus_root = _materialise_full(corpus_dir)
    manifest = json.loads(
        (corpus_root / "corpus-manifest.json").read_text(encoding="utf-8")
    )
    project_ids = sorted(str(p["id"]) for p in manifest.get("projects", []))

    tmp = Path(tempfile.mkdtemp(prefix="ecs-out-"))
    python_claim = _run_python(corpus_root, tmp / "python")
    ts_claim = _run_typescript(corpus_root, tmp / "typescript")

    identical = 0
    different: list[str] = []
    for pid in project_ids:
        py_bytes = _assertions_bytes(tmp / "python", pid)
        ts_bytes = _assertions_bytes(tmp / "typescript", pid)
        if py_bytes is not None and py_bytes == ts_bytes:
            identical += 1
        else:
            different.append(pid)

    return {
        "python": python_claim,
        "ts": ts_claim,
        "projects_total": len(project_ids),
        "projects_identical": identical,
        "different": different,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ecs",
        description="Two-engine ECS runner and assertions comparator (SPEC §11.5, P3.4).",
    )
    parser.add_argument(
        "--engines",
        default="python,typescript",
        help="comma-separated engines to run (currently python,typescript)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "corpus",
        help="the corpus directory or source tree",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="require every engine to claim full and every project's assertions to be identical",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    if sorted(engines) != ["python", "typescript"]:
        raise SystemExit(
            f"unsupported engines {engines!r}; expected python,typescript."
        )

    result = run_two_engine(args.corpus)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"python={result['python']} ts={result['ts']} "
            f"identical={result['projects_identical']}/{result['projects_total']}"
        )
        if result["different"]:
            print("different: " + ", ".join(result["different"][:10]))

    if args.compare:
        ok = (
            result["python"] == "full"
            and result["ts"] == "full"
            and result["projects_total"] > 0
            and result["projects_identical"] == result["projects_total"]
        )
        if ok:
            print(
                f"BYTE-IDENTICAL: {result['projects_identical']}/{result['projects_total']} "
                "projects, both engines claim full"
            )
            return 0
        print(
            f"NOT IDENTICAL: {result['projects_identical']}/{result['projects_total']} identical; "
            f"python={result['python']} ts={result['ts']}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
