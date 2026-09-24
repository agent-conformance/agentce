"""Runtime / continuous conformance gate (SPEC.md:249 DC-9/HR-10; SPEC.md:166 Art. 72).

AgentCE's own model of "continuous" assessment is a chronological, state-tracked series of real
``agentce assess --state <dir>`` invocations over an accumulating evidence stream (the mechanism the
incremental-state item already built for late-event handling and supersession, extended here to also
persist and diff each ``(subject, control)`` pair's outcome). This is repeated point-in-time
reassessment, never a persistent watch/daemon process -- a user, a scheduler, or a CI job supplies the
cadence; the engine supplies the state, the drift detection, and the report.

This check proves the mechanism for real by shelling the installed ``agentce`` console script three
times over three built-in-memory bundles (never by computing the diff itself or importing engine
internals, so it can only pass if the engine -- not this probe -- does the work):

1. a bundle with one ``Incident`` event naming the accountable actor: INC-02 (``spec/catalogs/base/
   eu-ai-act/controls/INC-02.yaml``) evaluates ``conformant``, and the state directory records it as
   this pair's first tracked outcome.
2. the identical bundle re-submitted (the pre-existing no-op case, SPEC §8.2 #8): zero drift.
3. step 1's events plus one new ``Incident`` event that omits the accountable actor: INC-02 flips to
   ``non-conformant`` through the existing evaluator, with no new evaluation primitive, and
   ``runtime_drift.jsonl`` names the pair, both outcomes, and the evidence event that caused it.

Determinism is checked by running the whole three-step scenario twice from two independent, fresh
state directories and comparing ``assertions.json`` and ``runtime_drift.jsonl`` at every step
(``manifest.json`` is correctly excluded -- it carries a wall-clock ``run.started_at`` field and is not
byte-identical run to run by design, exactly as ``tools/claims/register.yaml``'s ``engine.deterministic``
claim and ``conformance/ecs.py``'s canonical-set comparison already document).

    cd conformance && uv run --frozen python runtime_conformance_check.py              # the real gate
    cd conformance && uv run --frozen python runtime_conformance_check.py --self-test  # discrimination
    cd conformance && uv run --frozen python runtime_conformance_check.py --check-guard  # CI wiring

Offline, deterministic, no learned component. Standard library plus PyYAML (already a transitive
dependency of ``agent-conformance``, used here only to structurally parse ``.github/workflows/guards.yml``
for ``--check-guard``, the way ``tools/claims_check.py`` and ``tools/reusable_ci_artifacts_check.py``
already do).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
CATALOG_ID = "eu-ai-act@2026.09"
CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"
SUBJECT_A = "spiffe://corp/agents/runtime-agent-a"
SUBJECT_B = "spiffe://corp/agents/runtime-agent-b"
SOURCE = "urn:agentce:source:runtime-conformance:incidents"
CONTROL = "INC-02"
#: The real, installed `agentce` console script in *this* uv project's own environment (`conformance`
#: depends on `agent-conformance` as an editable path source) -- never `agentce.cli.main` imported
#: in-process, so this check can only pass if the engine actually does the work.
AGENTCE_BIN = Path(sys.prefix) / "bin" / "agentce"
GUARD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "guards.yml"
GUARD_JOB = "runtime-conformance"
RUN_TIMEOUT_S = 120


class SetupError(Exception):
    """The installed `agentce` console script could not be run at all."""


# --- building the scenario's bundles, in memory ---------------------------------------------------


def _incident(eid: str, subject: str, time: str, *, accountable: bool) -> dict[str, Any]:
    data: dict[str, Any] = {"@context": CONTEXT, "@type": "Incident"}
    if accountable:
        data["agent"] = {"id": subject}
    return {
        "specversion": "1.0",
        "id": eid,
        "source": SOURCE,
        "type": "org.agent-conformance.evidence.Incident.v1",
        "time": time,
        "subject": subject,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": "independent_system",
        "data": data,
    }


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in events
    )
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bundle(bundle: Path, events: list[dict[str, Any]]) -> None:
    rel = "events/incidents.jsonl"
    digest = _write_jsonl(bundle / rel, events)
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "runtime-conformance",
        "sources": [{"id": SOURCE}],
        "files": [{"path": rel, "sha256": digest}],
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _write_profile(path: Path, subjects: list[str]) -> None:
    lines = [
        "profile_version: 1",
        "observation_window:",
        '  start: "2026-05-01T00:00:00Z"',
        '  end: "2026-08-29T00:00:00Z"',
        "catalogs:",
        f'  - "{CATALOG_ID}"',
        "subjects:",
    ]
    for subject in subjects:
        lines += [
            f'  - id: "{subject}"',
            '    role: "both"',
            "    evidence_sources:",
            '      - adapter: "register"',
            f'        source: "{SOURCE}"',
            '        class: "independent_system"',
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- driving the real, installed CLI ---------------------------------------------------------------


def _agentce(args: list[str]) -> subprocess.CompletedProcess[str]:
    exe = str(AGENTCE_BIN)
    try:
        return subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise SetupError(
            f"cannot run the installed agentce console script at {exe} ({exc}); "
            "run `uv sync` in conformance/ (or `uv run --frozen true`)."
        ) from exc


def _assess(bundle: Path, profile: Path, out: Path, state: Path) -> dict[str, Any]:
    proc = _agentce(
        [
            "assess",
            "--bundle",
            str(bundle),
            "--profile",
            str(profile),
            "--catalog",
            CATALOG_ID,
            "--catalog-dir",
            str(CATALOG_DIR),
            "--out",
            str(out),
            "--state",
            str(state),
            "--json",
        ]
    )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SetupError(
            f"agentce assess printed no JSON (exit {proc.returncode}): {proc.stderr.strip()!r}"
        ) from exc
    # exit 0 (ok) or 1 (findings, e.g. step 3's non-conformant pair) are both a real, completed run;
    # anything else (a setup/input error) means this check cannot conclude anything about the engine.
    if envelope.get("exit_code") not in (0, 1):
        raise SetupError(f"agentce assess did not complete: {envelope}")
    return envelope


def _drift_lines(out: Path) -> list[dict[str, Any]]:
    path = out / "runtime_drift.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _assertions(out: Path) -> list[dict[str, Any]]:
    return json.loads((out / "assertions.json").read_text(encoding="utf-8"))


def _inc02(assertions: list[dict[str, Any]], subject: str) -> dict[str, Any] | None:
    for a in assertions:
        if a["control"] == CONTROL and a["subject"] == subject:
            return a
    return None


# --- C2: the real three-step scenario, plus its own determinism proof ------------------------------


def run_scenario(work: Path) -> dict[str, Any]:
    """Steps 1-3 in one state directory; returns each step's exit code, drift, and assertions."""
    profile = work / "profile.yaml"
    _write_profile(profile, [SUBJECT_A])
    state = work / "state"

    step1_events = [_incident("inc-a1", SUBJECT_A, "2026-05-02T09:00:00Z", accountable=True)]
    bundle1 = work / "bundle-1"
    _write_bundle(bundle1, step1_events)
    out1 = work / "out-1"
    env1 = _assess(bundle1, profile, out1, state)

    # Step 2: the identical bundle re-submitted -- the pre-existing no-op case (SPEC §8.2 #8).
    bundle2 = work / "bundle-2"
    _write_bundle(bundle2, step1_events)
    out2 = work / "out-2"
    env2 = _assess(bundle2, profile, out2, state)

    # Step 3: step 1's events plus one new Incident event that omits the accountable actor.
    step3_events = [
        *step1_events,
        _incident("inc-a2", SUBJECT_A, "2026-05-02T09:05:00Z", accountable=False),
    ]
    bundle3 = work / "bundle-3"
    _write_bundle(bundle3, step3_events)
    out3 = work / "out-3"
    env3 = _assess(bundle3, profile, out3, state)

    return {
        "step1": {"env": env1, "drift": _drift_lines(out1), "assertions": _assertions(out1)},
        "step2": {"env": env2, "drift": _drift_lines(out2), "assertions": _assertions(out2)},
        "step3": {"env": env3, "drift": _drift_lines(out3), "assertions": _assertions(out3)},
    }


def _scenario_problems(result: dict[str, Any]) -> list[str]:
    problems = []
    step1, step2, step3 = result["step1"], result["step2"], result["step3"]

    a1 = _inc02(step1["assertions"], SUBJECT_A)
    if a1 is None or a1["outcome"] != "conformant":
        problems.append(f"step 1: INC-02 for {SUBJECT_A} did not evaluate conformant: {a1}")
    if step1["drift"]:
        problems.append(f"step 1 (first tracked run) produced drift: {step1['drift']}")

    a2 = _inc02(step2["assertions"], SUBJECT_A)
    if a2 is None or a2["outcome"] != "conformant":
        problems.append(f"step 2: INC-02 for {SUBJECT_A} did not stay conformant: {a2}")
    if step2["drift"]:
        problems.append(f"step 2 (identical bundle, no-op) produced drift: {step2['drift']}")

    a3 = _inc02(step3["assertions"], SUBJECT_A)
    if a3 is None or a3["outcome"] != "non-conformant":
        problems.append(f"step 3: INC-02 for {SUBJECT_A} did not flip non-conformant: {a3}")
    expected_evidence = ["agentce:event/inc-a2"]
    if step3["drift"] != [
        {
            "control": CONTROL,
            "evidence": expected_evidence,
            "outcome": "non-conformant",
            "previous_outcome": "conformant",
            "subject": SUBJECT_A,
        }
    ]:
        problems.append(f"step 3 drift was {step3['drift']}, expected exactly one entry naming inc-a2")
    if step3["env"].get("exit_code") != 1:
        problems.append(f"step 3 (a non-conformant pair) did not exit 1 (findings): {step3['env']}")
    return problems


def check_scenario() -> str | None:
    """Run the scenario twice, from two independent fresh state directories, and require every step's
    ``assertions.json`` and ``runtime_drift.jsonl`` to be byte-identical between the two runs."""
    with tempfile.TemporaryDirectory(prefix="runtime-conformance-a-") as tmp_a:
        result_a = run_scenario(Path(tmp_a))
    with tempfile.TemporaryDirectory(prefix="runtime-conformance-b-") as tmp_b:
        result_b = run_scenario(Path(tmp_b))

    problems = _scenario_problems(result_a)
    for step in ("step1", "step2", "step3"):
        if result_a[step]["assertions"] != result_b[step]["assertions"]:
            problems.append(f"{step}: assertions.json was not byte-identical across two fresh runs")
        if result_a[step]["drift"] != result_b[step]["drift"]:
            problems.append(f"{step}: runtime_drift.jsonl was not byte-identical across two fresh runs")
    return "; ".join(problems) if problems else None


# --- C1: self-test — three named, discriminating negative cases ------------------------------------


def self_test_idempotent_rerun() -> str | None:
    """(a) Re-running the identical bundle digest against an existing state dir yields zero drift."""
    with tempfile.TemporaryDirectory(prefix="rtc-idempotent-") as tmp:
        work = Path(tmp)
        profile = work / "profile.yaml"
        _write_profile(profile, [SUBJECT_A])
        state = work / "state"
        events = [_incident("inc-a1", SUBJECT_A, "2026-05-02T09:00:00Z", accountable=True)]
        bundle = work / "bundle"
        _write_bundle(bundle, events)
        _assess(bundle, profile, work / "out-1", state)
        _assess(bundle, profile, work / "out-2", state)
        drift = _drift_lines(work / "out-2")
        if drift:
            return f"re-running an identical bundle digest produced drift: {drift}"
    return None


def self_test_pre_upgrade_state() -> str | None:
    """(b) A state dir with no `last_outcomes` key at all yields zero drift and does not raise."""
    with tempfile.TemporaryDirectory(prefix="rtc-pre-upgrade-") as tmp:
        work = Path(tmp)
        profile = work / "profile.yaml"
        _write_profile(profile, [SUBJECT_A])
        state = work / "state"
        state.mkdir()
        # Exactly what an engine older than this item ever wrote: a real prior assessment recorded,
        # but no per-(subject, control) outcome tracking.
        (state / "state.json").write_text(
            json.dumps(
                {
                    "state_version": 1,
                    "bundle_digests": ["sha256:" + "ab" * 32],
                    "last_report_digest": "sha256:" + "cd" * 32,
                    "last_window_end": "2026-01-01T00:00:00Z",
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        events = [_incident("inc-a1", SUBJECT_A, "2026-05-02T09:00:00Z", accountable=True)]
        bundle = work / "bundle"
        _write_bundle(bundle, events)
        out = work / "out"
        _assess(bundle, profile, out, state)
        drift = _drift_lines(out)
        if drift:
            return f"a pre-upgrade state directory (no last_outcomes key) produced drift: {drift}"
    return None


def self_test_two_pair() -> str | None:
    """(c) Of two tracked (subject, control) pairs, only the one whose outcome changed is reported."""
    with tempfile.TemporaryDirectory(prefix="rtc-two-pair-") as tmp:
        work = Path(tmp)
        profile = work / "profile.yaml"
        _write_profile(profile, [SUBJECT_A, SUBJECT_B])
        state = work / "state"
        t0_events = [
            _incident("inc-a1", SUBJECT_A, "2026-05-02T09:00:00Z", accountable=True),
            _incident("inc-b1", SUBJECT_B, "2026-05-02T09:00:00Z", accountable=True),
        ]
        bundle0 = work / "bundle-0"
        _write_bundle(bundle0, t0_events)
        _assess(bundle0, profile, work / "out-0", state)
        t1_events = [
            *t0_events,
            _incident("inc-a2", SUBJECT_A, "2026-05-02T09:05:00Z", accountable=False),
        ]
        bundle1 = work / "bundle-1"
        _write_bundle(bundle1, t1_events)
        out1 = work / "out-1"
        _assess(bundle1, profile, out1, state)
        pairs = {(d["subject"], d["control"]) for d in _drift_lines(out1)}
        expected = {(SUBJECT_A, CONTROL)}
        if pairs != expected:
            return f"two-pair scenario reported drift for {pairs}, expected exactly {expected}"
    return None


def self_test() -> str | None:
    problems = [
        p
        for p in (
            self_test_idempotent_rerun(),
            self_test_pre_upgrade_state(),
            self_test_two_pair(),
        )
        if p is not None
    ]
    return "; ".join(problems) if problems else None


# --- C4: the CI guard is real, not a grep over the workflow's own prose -----------------------------


#: A parsed `guards.yml`: PyYAML's YAML-1.1 resolver reads the unquoted `on:` key as the boolean
#: `True`, not the string `"on"`, so the top-level mapping is not reliably `dict[str, Any]`.
WorkflowDoc = dict[Any, Any]


def _workflow_triggers(doc: WorkflowDoc) -> set[str]:
    # PyYAML's default (YAML 1.1) resolver reads the unquoted key `on:` as the boolean `True`, not the
    # string "on" -- GitHub Actions workflows hit this on every parse, so both keys are checked.
    on = doc.get("on", doc.get(True, {}))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(t) for t in on}
    if isinstance(on, dict):
        return {str(t) for t in on}
    return set()


def _job_run_texts(job: dict[str, Any]) -> list[str]:
    steps = job.get("steps", [])
    return [str(s["run"]) for s in steps if isinstance(s, dict) and "run" in s]


def check_guard(doc: WorkflowDoc) -> str | None:
    jobs = doc.get("jobs", {})
    job = jobs.get(GUARD_JOB) if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        return f"no jobs.{GUARD_JOB} in the guard workflow"
    if not _workflow_triggers(doc) & {"push", "pull_request"}:
        return f"jobs.{GUARD_JOB}'s workflow does not trigger on push or pull_request"
    run_texts = _job_run_texts(job)
    has_self_test = any(
        "runtime_conformance_check.py" in t and "--self-test" in t for t in run_texts
    )
    has_real_run = any(
        "runtime_conformance_check.py" in t and "--self-test" not in t for t in run_texts
    )
    if not has_self_test:
        return f"jobs.{GUARD_JOB} never runs runtime_conformance_check.py --self-test"
    if not has_real_run:
        return f"jobs.{GUARD_JOB} never runs the real scenario (only --self-test)"
    return None


def _load_guard_workflow(path: Path) -> WorkflowDoc:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) else {}


def self_test_guard() -> str | None:
    good = _load_guard_workflow(GUARD_WORKFLOW)
    if check_guard(good) is not None:
        return f"the real guard workflow did not pass check_guard: {check_guard(good)}"

    missing = {k: v for k, v in good.items() if k != "jobs"}
    missing["jobs"] = {k: v for k, v in good.get("jobs", {}).items() if k != GUARD_JOB}
    if check_guard(missing) is None:
        return "bad fixture 'job missing entirely' did not fail check_guard"

    wrong_trigger = {k: v for k, v in good.items() if k not in ("on", True)}
    wrong_trigger["on"] = {"workflow_dispatch": {}}
    if check_guard(wrong_trigger) is None:
        return "bad fixture 'workflow_dispatch only' did not fail check_guard"

    self_test_only = {k: v for k, v in good.items() if k != "jobs"}
    self_test_only["jobs"] = dict(good.get("jobs", {}))
    self_test_only["jobs"][GUARD_JOB] = {
        "runs-on": "ubuntu-latest",
        "steps": [{"run": "python runtime_conformance_check.py --self-test"}],
    }
    if check_guard(self_test_only) is None:
        return "bad fixture 'only the self-test step' did not fail check_guard"

    return None


# --- CLI ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runtime_conformance_check",
        description="Runtime/continuous conformance gate: outcome-drift detection over a state-tracked "
        "series of real agentce assess runs (SPEC.md:249 DC-9/HR-10).",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="prove the negative cases discriminate (C1)"
    )
    parser.add_argument(
        "--check-guard",
        action="store_true",
        help="structurally verify the CI guard job wires the self-test and the real scenario (C4)",
    )
    args = parser.parse_args(argv)

    if args.check_guard:
        if args.self_test:
            problem = self_test_guard()
            if problem is not None:
                print(f"FAIL {problem}", file=sys.stderr)
                return 1
            print("GUARD SELF-TEST PASSED")
            return 0
        doc = _load_guard_workflow(GUARD_WORKFLOW)
        problem = check_guard(doc)
        if problem is not None:
            print(f"FAIL {problem}", file=sys.stderr)
            return 1
        print("GUARD OK")
        return 0

    if args.self_test:
        try:
            problem = self_test()
        except SetupError as exc:
            print(f"FAIL setup: {exc}", file=sys.stderr)
            return 2
        if problem is not None:
            print(f"FAIL {problem}", file=sys.stderr)
            return 1
        print("SELF-TEST PASSED")
        return 0

    try:
        problem = check_scenario()
    except SetupError as exc:
        print(f"FAIL setup: {exc}", file=sys.stderr)
        return 2
    if problem is not None:
        print(f"FAIL {problem}", file=sys.stderr)
        return 1
    print("RUNTIME-CONFORMANCE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
