"""ingest_trust_parity_check - prove Python, TypeScript, and Java all refuse to trust an undeclared
bundle source's own self-asserted trust class (SPEC §6.4, item 18.31), and that the correction never
touches the bytes integrity verification hashes (SPEC §6.6).

Three independent per-engine unit-test suites (18.31's C1/C2/C3) can each pass while the real,
installed binaries disagree, since none of them ever runs another engine's own artifact and compares
(the same gap `verify_parity_check.py` and `assess_exit_code_parity_check.py` close for `verify` and
`assess`'s exit code). This check drives all three the way a user does, over one evidence bundle whose
only source is undeclared (listed in the manifest with no `class`) and whose one event -- the spec's
own worked `ApprovalDecided` example, `spec/model/examples/appendix-g/event-2.json` -- self-asserts
the highest ladder rung, `independent_system`, with a real, unbroken `export_chained` integrity hash:

* `agentce verify --bundle` must report `verified_weak` (not `failed`) on all three engines -- proof
  that the trust correction reads a separate, byte-identical copy of the event rather than mutating the
  one integrity verification hashes.
* `agentce assess --bundle` must write an `activity.json` whose `approvals_by_recorder` counts this
  approval under `self_report`, not `independent_system` -- `ApprovalDecided` is the one event type
  every engine's activity summary buckets directly by `agentcesourceclass` (`activity.py:118`,
  mirrored in TypeScript/Java), so this is a direct, unambiguous read of the corrected field a user
  sees in a real report artifact, with no applicability/population machinery in between.

* `--self-test` needs no build: it proves the comparator (a synthetic pair of "engine output" strings,
  identical then a one-byte tamper, must be told apart), the same discipline every other parity check
  in this directory uses.
* The default (no flag) mode builds the fixture once (a single shelled call into `engines/python`'s own
  `uv` environment, the only place `agentce.canonical`'s real RFC 8785 hash function lives), copies it
  byte-identical into three per-engine directories, and runs all three installed engines.

Usage:
    ingest_trust_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java)
    ingest_trust_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
QUICKSTART = ROOT / "corpus" / "quickstart"

#: The spec's own worked `ApprovalDecided` example (`spec/model/examples/appendix-g/event-2.json`),
#: which self-asserts `"agentcesourceclass": "independent_system"` -- reused rather than invented, so
#: this fixture claims a trust class the same way the spec's own worked example does. Quickstart has
#: no `ApprovalDecided` event of its own (grep confirms), so injecting one adds a clean, unambiguous
#: signal rather than perturbing an existing count.
EXAMPLE_EVENT = ROOT / "spec" / "model" / "examples" / "appendix-g" / "event-2.json"

#: A single, bare `ApprovalDecided` event (no other evidence) makes every control `not_applicable`
#: and `assess` exits 3 having reached no outcome at all -- there is no report to read. So this fixture
#: is the real, committed `corpus/quickstart` evidence bundle (already rich enough to reach 49 real
#: assertions) plus one extra, undeclared source carrying the injected event: everything else about
#: quickstart is untouched.
#:
#: Builds, in `agentce.canonical`'s own `uv` environment (the one place its real RFC 8785 hash
#: function lives), the injected event with a freshly recomputed, genuinely valid `export_chained`
#: integrity hash (genesis `prev`, no `sig_ref`, its own one-event stream) -- so `verify --bundle` has
#: something real to verify, not a doctored fixture a correct implementation would reject for an
#: unrelated reason -- and copies quickstart's `applicability.yaml`/`domain.linkml.yaml` alongside it.
_BUILD_SCRIPT = """
import json
import hashlib
import shutil
from pathlib import Path

from agentce import canonical

example = json.loads(Path({example!r}).read_text(encoding="utf-8"))
assert example["agentcesourceclass"] == "independent_system", example["agentcesourceclass"]

event = json.loads(json.dumps(example))
event["subject"] = "spiffe://corp/agents/credit-langgraph"  # quickstart's own subject
integrity = event["data"]["integrity"]
integrity["prev"] = "0" * 64
integrity["strength"] = "export_chained"
integrity["stream"] = event["source"] + "|" + event["subject"]
integrity.pop("sig_ref", None)
without = json.loads(json.dumps(event))
without["data"].pop("integrity", None)
integrity["hash"] = canonical.sha256_hex(without)

out = Path({out!r})
bundle = out / "bundle"
shutil.copytree({quickstart_evidence!r}, bundle)
shutil.copy({applicability!r}, out / "applicability.yaml")
shutil.copy({domain!r}, out / "domain.linkml.yaml")

events_file = bundle / "events" / "injected-approvals-jira.jsonl"
events_file.write_text(json.dumps(event) + "\\n", encoding="utf-8")

manifest_path = bundle / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["files"].append(
    {{
        "path": "events/injected-approvals-jira.jsonl",
        "sha256": hashlib.sha256(events_file.read_bytes()).hexdigest(),
    }}
)
assert not any(s["id"] == event["source"] for s in manifest["sources"]), "source already declared"
manifest["sources"].append({{"id": event["source"]}})  # undeclared: no "class"
manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

print(json.dumps({{"source": event["source"], "stream": integrity["stream"]}}))
"""


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[str, int]:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.stdout, proc.returncode


def build_fixture(dest: Path) -> str:
    """Writes the fixture to `dest` and returns the injected event's integrity stream id."""
    script = _BUILD_SCRIPT.format(
        example=str(EXAMPLE_EVENT),
        out=str(dest),
        quickstart_evidence=str(QUICKSTART / "evidence"),
        applicability=str(QUICKSTART / "applicability.yaml"),
        domain=str(QUICKSTART / "domain.linkml.yaml"),
    )
    out, code = _run(
        ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "python", "-c", script],
        cwd=ROOT,
    )
    if code != 0:
        raise SystemExit(f"could not build the ingest-trust fixture: {out}")
    return json.loads(out)["stream"]


def python_cli(args: list[str]) -> tuple[str, int]:
    return _run(
        ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "agentce", *args],
        cwd=ROOT,
    )


def typescript_cli(args: list[str]) -> tuple[str, int]:
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(["node", str(entry), *args], cwd=ROOT)


def java_cli(args: list[str]) -> tuple[str, int]:
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built (run `./gradlew :assemble -q` in engines/java first)"
        )
    return _run(["java", "-jar", str(jars[-1]), *args], cwd=ROOT)


ENGINES = {"python": python_cli, "typescript": typescript_cli, "java": java_cli}


def compare(
    label: str, a: str, b: str, engine_a: str, engine_b: str, failures: list[str]
) -> None:
    if a != b:
        failures.append(f"{label}: {engine_a} and {engine_b} disagree ({a!r} vs {b!r})")


def self_test() -> int:
    failures: list[str] = []
    unmoved: list[str] = []
    compare("comparator-self-test", "x", "x", "a", "b", unmoved)
    if unmoved:
        failures.append("comparator wrongly flagged two identical values as a mismatch")
    caught: list[str] = []
    compare("comparator-self-test", "x", "y", "a", "b", caught)
    if not caught:
        failures.append("comparator failed to catch a one-value tamper")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("ingest_trust_parity_check self-test: comparator discriminates")
    return 1 if failures else 0


def run_real_check() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ingest-trust-parity-") as raw:
        canonical_fixture = Path(raw) / "canonical"
        injected_stream = build_fixture(canonical_fixture)

        per_engine: dict[str, Path] = {}
        for engine in ENGINES:
            d = Path(raw) / engine
            shutil.copytree(canonical_fixture, d)
            per_engine[engine] = d

        # 1. `verify --bundle`: the untouched raw event, with a genuinely valid chain, still
        # verifies on every engine. If the trust correction mutated the hashed event in place
        # instead of reading a separate raw copy, this would report `failed`. Quickstart's bundle
        # has several streams; pick out the one this fixture injected.
        verify_runs = {
            engine: ENGINES[engine](["verify", "--bundle", str(d / "bundle"), "--json"])
            for engine, d in per_engine.items()
        }
        statuses: dict[str, str] = {}
        for engine, (out, code) in verify_runs.items():
            if code != 0:
                failures.append(
                    f"verify:{engine}: exit {code}, expected 0 ({out.strip()[:200]!r})"
                )
                continue
            try:
                streams = json.loads(out)["streams"]
                injected = next(s for s in streams if s["stream"] == injected_stream)
            except (json.JSONDecodeError, KeyError, StopIteration) as exc:
                failures.append(
                    f"verify:{engine}: could not find stream {injected_stream!r} in {out!r} ({exc})"
                )
                continue
            statuses[engine] = injected["status"]
        for engine, status in statuses.items():
            if status != "verified_weak":
                failures.append(
                    f"verify:{engine}: stream status is {status!r}, expected 'verified_weak'"
                )
        names = list(statuses)
        for a, b in zip(names, names[1:]):
            compare("verify:status", statuses[a], statuses[b], a, b, failures)

        # 2. `assess --bundle`: the report never trusts the self-assertion. `activity.json` must
        # bucket this ApprovalDecided event's recorder under self_report, never independent_system
        # (the class it claims), byte-identical across engines.
        activity: dict[str, str] = {}
        for engine, d in per_engine.items():
            out_dir = d / "out"
            out, code = ENGINES[engine](
                [
                    "assess",
                    "--bundle",
                    str(d / "bundle"),
                    "--profile",
                    str(d / "applicability.yaml"),
                    "--domain",
                    str(d / "domain.linkml.yaml"),
                    "--out",
                    str(out_dir),
                    "--json",
                ]
            )
            if code not in (0, 1, 2):
                failures.append(
                    f"assess:{engine}: exit {code}, expected 0, 1 or 2 (a real assessment outcome; "
                    f"3 means assess reached no outcome at all) ({out.strip()[:200]!r})"
                )
                continue
            activity_file = out_dir / "activity.json"
            if not activity_file.is_file():
                failures.append(f"assess:{engine}: {activity_file} was not written")
                continue
            activity[engine] = activity_file.read_text(encoding="utf-8")
        for engine, text in activity.items():
            try:
                recorder = json.loads(text)["approvals_by_recorder"]
            except (json.JSONDecodeError, KeyError) as exc:
                failures.append(
                    f"assess:{engine}: could not read approvals_by_recorder from {text!r} ({exc})"
                )
                continue
            if recorder.get("self_report") != 1:
                failures.append(
                    f"assess:{engine}: approvals_by_recorder.self_report is "
                    f"{recorder.get('self_report')!r}, expected 1"
                )
            if recorder.get("independent_system") != 0:
                failures.append(
                    f"assess:{engine}: approvals_by_recorder.independent_system is "
                    f"{recorder.get('independent_system')!r}, expected 0 (the raw event claims "
                    "'independent_system', but its source is undeclared)"
                )
        names = list(activity)
        for a, b in zip(names, names[1:]):
            compare("assess:activity.json", activity[a], activity[b], a, b, failures)

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "MATCH: an undeclared source's self-asserted independent_system is corrected to self_report "
        "in assess's activity.json while verify --bundle still reports verified_weak on the untouched "
        "raw event, byte-identical across python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest_trust_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
