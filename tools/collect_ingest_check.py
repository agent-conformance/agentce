#!/usr/bin/env python3
"""collect_ingest_check - prove the file-based path into AgentCE works for real.

Four independent facts, none standing in for another:

1. **ingest** -- ``agentce ingest`` turns a real, already-committed adapter export
   (``adapters/otel-genai/fixtures/otel-genai-chat/input.json``) into a bundle ``agentce validate``
   accepts with zero quarantines.
2. **collect** -- a real (non-dry-run) ``agentce collect`` run against the shipped example config
   (``adapters/fixtures/collect-dry-run.yaml``) completes every source, by reading each one's local
   ``export`` (SPEC §5.4).
3. **collector component** -- an ADR under ``docs/adr/`` names an OpenTelemetry Collector component and
   a real, non-empty, collector-shaped artifact it points at actually exists (ADR 0017,
   ``adapters/otel-genai/otel-collector/config.yaml``).
4. **hardened input** -- a deeply nested export or collect config gets the same deliberate, named
   ``input.*`` refusal the rest of the engine already gives untrusted, bundle-adjacent input, never a
   raw Python traceback (loophole L13.1).

    collect_ingest_check.py              check the repository (the invocation the CI job uses)
    collect_ingest_check.py --self-test  prove each fact discriminates: good input passes, and a
                                          synthetic bad input fails for exactly the rule it targets

It imports ``agentce`` to run ``ingest``/``collect``/``validate`` in-process, so run it in the engine's
environment (``uv run --project engines/python --frozen python tools/collect_ingest_check.py``).
Standard library only otherwise; no network, no learned component.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INGEST_FIXTURE = "adapters/otel-genai/fixtures/otel-genai-chat/input.json"
COLLECT_CONFIG = "adapters/fixtures/collect-dry-run.yaml"
ADR_DIR = "docs/adr"
COLLECTOR_MARKERS = re.compile(
    r"opentelemetry\s*collector|otel\s*collector|otelcol", re.IGNORECASE
)


def _run_cli(argv: list[str]) -> tuple[int, dict[str, object]]:
    """Run ``agentce`` in-process (as ``tools/collect_check.py`` does) and parse its ``--json`` output."""
    cli = importlib.import_module("agentce.cli")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main([*argv, "--json"])
    try:
        return code, json.loads(out.getvalue())
    except ValueError:
        return code, {}


def check_ingest(
    root: Path, fixture: str = INGEST_FIXTURE, *, adapters_root: Path | None = None
) -> str | None:
    """``agentce ingest`` turns ``fixture`` into a bundle ``agentce validate`` accepts."""
    fixture_path = root / fixture
    if not fixture_path.is_file():
        return f"{fixture} is missing"
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "bundle"
        code, env = _run_cli(
            [
                "ingest",
                "--in",
                str(fixture_path),
                "--out",
                str(out_dir),
                "--adapter",
                "otel-genai",
                "--adapters-root",
                str(adapters_root if adapters_root is not None else root / "adapters"),
            ]
        )
        if code != 0:
            return f"agentce ingest --in {fixture} exited {code}: {env}"
        events = env.get("events", 0)
        if not isinstance(events, int) or events < 1:
            return f"agentce ingest --in {fixture} produced no events"
        code, env = _run_cli(["validate", "--bundle", str(out_dir)])
        if code != 0 or env.get("quarantined"):
            return f"agentce validate on ingest's own output did not accept it cleanly: {env}"
    return None


def _error_of(env: dict[str, object]) -> dict[str, object]:
    error = env.get("error")
    return error if isinstance(error, dict) else {}


def check_hardened_input(root: Path, *, adapters_root: Path | None = None) -> str | None:
    """A deeply nested export or collect config is a deliberate ``input.*`` refusal, never a raw
    Python traceback (loophole L13.1: ``ingest``/``collect`` get the same hardening the rest of the
    engine already gives untrusted, bundle-adjacent input)."""
    adapters_dir = adapters_root if adapters_root is not None else root / "adapters"
    with tempfile.TemporaryDirectory() as tmp:
        export = Path(tmp) / "deep-export.json"
        # json's C accelerator tolerates far deeper nesting than PyYAML's parser before it raises
        # RecursionError; 6000 (enough for the YAML config below) is not enough here.
        export.write_text("[" * 20_000 + "]" * 20_000, encoding="utf-8")
        code, env = _run_cli(
            [
                "ingest",
                "--in",
                str(export),
                "--out",
                str(Path(tmp) / "bundle"),
                "--adapter",
                "otel-genai",
                "--adapters-root",
                str(adapters_dir),
            ]
        )
        cause = str(_error_of(env).get("cause", ""))
        if code == 0 or "Traceback (most recent call last)" in cause or "too deep" not in cause:
            return f"a deeply nested ingest export did not get a deliberate refusal: exit {code}, {cause!r}"

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "deep-collect.yaml"
        config.write_text("a: " + "[" * 6000 + "]" * 6000 + "\n", encoding="utf-8")
        code, env = _run_cli(
            ["collect", "--config", str(config), "--out", str(Path(tmp) / "bundle")]
        )
        error = _error_of(env)
        if code == 0 or error.get("key") != "input.collect_config":
            return f"a deeply nested collect config did not get input.collect_config: exit {code}, {error!r}"
    return None


def check_collect(root: Path, config: str = COLLECT_CONFIG) -> str | None:
    """A real ``agentce collect`` run against ``config`` completes every source."""
    config_path = root / config
    if not config_path.is_file():
        return f"{config} is missing"
    with tempfile.TemporaryDirectory() as tmp:
        code, env = _run_cli(
            [
                "collect",
                "--config",
                str(config_path),
                "--out",
                str(Path(tmp) / "bundle"),
            ]
        )
        sources_raw = env.get("sources", [])
        sources = sources_raw if isinstance(sources_raw, list) else []
        incomplete = [
            s.get("id")
            for s in sources
            if isinstance(s, dict) and s.get("completeness") == "incomplete"
        ]
        if code != 0 or not sources or incomplete:
            return (
                f"a real collect run against {config} did not complete every source "
                f"(exit {code}, incomplete: {incomplete})"
            )
    return None


def check_collector_component(root: Path, adr_dir: str = ADR_DIR) -> str | None:
    """An ADR names an OTel Collector component and points at a real, non-empty artifact."""
    directory = root / adr_dir
    hit = None
    for candidate in sorted(directory.glob("*.md")) if directory.is_dir() else []:
        text = candidate.read_text(encoding="utf-8")
        if COLLECTOR_MARKERS.search(text):
            hit = (candidate, text)
            break
    if hit is None:
        return f"no {adr_dir}/*.md mentions an OpenTelemetry Collector component"
    adr_path, text = hit
    candidates = re.findall(r"`([\w./-]+/[\w./-]+)`", text)
    collector_like = [
        c for c in candidates if "collector" in c.lower() or "otelcol" in c.lower()
    ]
    for candidate in collector_like:
        path = root / candidate
        if path.is_file() and path.stat().st_size > 0:
            return None
        if path.is_dir() and any(path.rglob("*")):
            return None
    return f"{adr_path} names an OTel Collector component but no real, non-empty artifact for it"


def check(root: Path = ROOT) -> list[str]:
    problems = [
        problem
        for problem in (
            check_ingest(root),
            check_collect(root),
            check_collector_component(root),
            check_hardened_input(root),
        )
        if problem is not None
    ]
    return problems


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    problems = check(ROOT)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        "collect_ingest_check: ok — ingest, collect, and the collector component all work"
    )
    return 0


# --- self-test ---------------------------------------------------------------------------------------


def _self_test_ingest() -> bool:
    ok = True
    if check_ingest(ROOT) is not None:
        print("self-test ingest-good-tree: FAIL")
        ok = False
    else:
        print("self-test ingest-good-tree: ok")
    with tempfile.TemporaryDirectory() as tmp:
        bad_root = Path(tmp)
        bad_fixture = bad_root / INGEST_FIXTURE
        bad_fixture.parent.mkdir(parents=True, exist_ok=True)
        bad_fixture.write_text(
            "{}", encoding="utf-8"
        )  # an export with no spans: zero events
        # A real, working adapter environment (the repo's own), so the only variable is the export.
        problem = check_ingest(bad_root, adapters_root=ROOT / "adapters")
        if problem is None:
            print("self-test ingest-empty-export: FAIL")
            ok = False
        else:
            print("self-test ingest-empty-export: ok")
    return ok


def _self_test_collect() -> bool:
    ok = True
    if check_collect(ROOT) is not None:
        print("self-test collect-good-tree: FAIL")
        ok = False
    else:
        print("self-test collect-good-tree: ok")
    with tempfile.TemporaryDirectory() as tmp:
        bad_root = Path(tmp)
        config = bad_root / "adapters" / "fixtures" / "collect-dry-run.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "job:\n  id: j\n  principal: p\n"
            "sources:\n  - id: urn:x\n    adapter: otel-genai\n    endpoint: https://x\n",
            encoding="utf-8",
        )  # no `export`: the source has no connector, so it stays incomplete
        problem = check_collect(bad_root)
        if problem is None:
            print("self-test collect-no-export-stays-incomplete: FAIL")
            ok = False
        else:
            print("self-test collect-no-export-stays-incomplete: ok")
    return ok


def _self_test_collector_component() -> bool:
    ok = True
    if check_collector_component(ROOT) is not None:
        print("self-test collector-component-good-tree: FAIL")
        ok = False
    else:
        print("self-test collector-component-good-tree: ok")
    with tempfile.TemporaryDirectory() as tmp:
        bad_root = Path(tmp)
        (bad_root / "docs" / "adr").mkdir(parents=True)
        problem = check_collector_component(bad_root)
        if problem is None:
            print("self-test collector-component-no-adr: FAIL")
            ok = False
        else:
            print("self-test collector-component-no-adr: ok")
    with tempfile.TemporaryDirectory() as tmp:
        bad_root = Path(tmp)
        adr_dir = bad_root / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "0001-x.md").write_text(
            "# An OpenTelemetry Collector plan\n\nSee `docs/adr/0001-x.md` for details.\n",
            encoding="utf-8",
        )  # mentions the Collector but names no collector-shaped artifact
        problem = check_collector_component(bad_root)
        if problem is None:
            print("self-test collector-component-no-artifact: FAIL")
            ok = False
        else:
            print("self-test collector-component-no-artifact: ok")
    return ok


def _self_test_hardened_input() -> bool:
    ok = True
    if check_hardened_input(ROOT) is not None:
        print("self-test hardened-input-deep-export-and-config-refused: FAIL")
        ok = False
    else:
        print("self-test hardened-input-deep-export-and-config-refused: ok")
    return ok


def self_test() -> int:
    results = [
        _self_test_ingest(),
        _self_test_collect(),
        _self_test_collector_component(),
        _self_test_hardened_input(),
    ]
    ok = all(results)
    print(f"collect_ingest_check self-test: {'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
