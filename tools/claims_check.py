#!/usr/bin/env python3
"""claims_check - keep the public-claims register honest (see ADR-0010).

The register at ``tools/claims/register.yaml`` names every public claim the project makes, the pages
that state it, whether it is ``built`` or ``roadmap``, and - for a built claim - the CI check that
proves it. This tool enforces the register's integrity so that:

* a claim can never be marked ``built`` without a wired CI check (a workflow that exists, names a
  real job, and runs on pull_request or push);
* a referenced page or workflow can never silently disappear;
* roadmap material can never be worded as if it had shipped.

    claims_check.py              validate tools/claims/register.yaml against the repository
                                 (exit 1 on any violation)
    claims_check.py --self-test  prove the checker discriminates: a good register passes and each
                                 bad fixture fails on exactly the rule it targets

Uses PyYAML (``safe_load`` only, per ADR-0005). No network, no learned component.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent

VALID_STATUS = ("built", "roadmap")
# A roadmap claim's text must read as forward-looking: it names one of these qualifiers so it can
# never be mistaken for a shipped capability. Kept small and explicit for determinism.
ROADMAP_QUALIFIERS = (
    "planned",
    "roadmap",
    "will ",
    "not yet",
    "upcoming",
    "future",
    "intended",
    "once ",
)


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _trigger_events(wf: Any) -> set[str]:
    """Return the trigger event names of a parsed workflow.

    YAML 1.1 reads a bare ``on:`` key as the boolean ``True``, so accept either key, and normalise
    the mapping / list / scalar forms the ``on`` value can take.
    """
    if not isinstance(wf, dict):
        return set()
    trig = wf.get("on", wf.get(True))
    if isinstance(trig, dict):
        return {str(k) for k in trig}
    if isinstance(trig, list):
        return {str(x) for x in trig}
    if isinstance(trig, str):
        return {trig}
    return set()


def _workflow_jobs(wf: Any) -> set[str]:
    if isinstance(wf, dict) and isinstance(wf.get("jobs"), dict):
        return {str(k) for k in wf["jobs"]}
    return set()


def _check_wired(loc: str, check: Any, root: Path) -> list[str]:
    """Validate that a built claim's ``check`` names a real CI job that runs on PRs."""
    out: list[str] = []
    if not isinstance(check, dict):
        return [
            f"{loc}: built claim has no check (a built claim needs a wired CI check)"
        ]
    wf_rel = check.get("workflow")
    job = check.get("job")
    if not isinstance(wf_rel, str) or not wf_rel:
        out.append(f"{loc}: check.workflow is missing")
        return out
    if not isinstance(job, str) or not job:
        out.append(f"{loc}: check.job is missing")
    wf_path = root / wf_rel
    if not wf_path.exists():
        out.append(f"{loc}: check.workflow not found: {wf_rel}")
        return out
    try:
        wf = _load_yaml(wf_path)
    except yaml.YAMLError as exc:
        out.append(f"{loc}: check.workflow is not valid YAML: {exc}")
        return out
    events = _trigger_events(wf)
    if not ({"push", "pull_request"} & events):
        out.append(
            f"{loc}: check.workflow {wf_rel} does not run on pull_request or push "
            f"(events: {sorted(events)})"
        )
    if isinstance(job, str) and job:
        jobs = _workflow_jobs(wf)
        if job not in jobs:
            out.append(
                f"{loc}: check.job {job!r} not found in {wf_rel} (jobs: {sorted(jobs)})"
            )
    return out


def check_register(data: Any, root: Path) -> list[str]:
    """Return every integrity violation in a claims register (empty list means valid)."""
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return ["register: top-level must be a mapping with a 'claims' list"]

    violations: list[str] = []
    seen_ids: set[str] = set()
    for i, claim in enumerate(data["claims"]):
        loc = f"claim[{i}]"
        if not isinstance(claim, dict):
            violations.append(f"{loc}: must be a mapping")
            continue

        cid = claim.get("id")
        if isinstance(cid, str) and cid:
            loc = f"claim {cid!r}"
            if cid in seen_ids:
                violations.append(f"{loc}: duplicate id")
            seen_ids.add(cid)
        else:
            violations.append(f"{loc}: missing or invalid 'id'")

        text = claim.get("text")
        if not isinstance(text, str) or not text.strip():
            violations.append(f"{loc}: missing or empty 'text'")

        pages = claim.get("pages", [])
        if not isinstance(pages, list):
            violations.append(f"{loc}: 'pages' must be a list")
            pages = []

        status = claim.get("status")
        if status not in VALID_STATUS:
            violations.append(
                f"{loc}: 'status' must be one of {list(VALID_STATUS)}, got {status!r}"
            )
            continue  # status-dependent rules cannot run without a valid status

        for page in pages:
            if not isinstance(page, str):
                violations.append(f"{loc}: page entry must be a string, got {page!r}")
            elif not (root / page).exists():
                violations.append(f"{loc}: page not found: {page}")

        if status == "built":
            violations.extend(_check_wired(loc, claim.get("check"), root))
        elif status == "roadmap":
            if isinstance(text, str) and text.strip():
                lowered = text.lower()
                if not any(q in lowered for q in ROADMAP_QUALIFIERS):
                    violations.append(
                        f"{loc}: roadmap claim is worded as shipped (its text needs a roadmap "
                        f"qualifier such as 'planned', 'will', or 'not yet')"
                    )
    return violations


def find_register() -> Path:
    """Locate tools/claims/register.yaml whether installed editable or run in place."""
    beside = HERE / "claims" / "register.yaml"
    if beside.exists():
        return beside
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / "tools" / "claims" / "register.yaml"
        if candidate.exists():
            return candidate
    return beside


def run_check() -> int:
    register = find_register()
    if not register.exists():
        print(f"CLAIMS FAILED: register not found at {register}")
        return 1
    root = register.parents[2]  # <root>/tools/claims/register.yaml -> <root>
    data = _load_yaml(register)
    violations = check_register(data, root)
    if violations:
        print("CLAIMS FAILED:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    claims = data["claims"]
    built = sum(1 for c in claims if c.get("status") == "built")
    roadmap = sum(1 for c in claims if c.get("status") == "roadmap")
    print(f"CLAIMS OK ({len(claims)} claims, {built} built, {roadmap} roadmap)")
    return 0


def self_test() -> int:
    """Run the checker over the good and bad fixtures and prove each bad fixture is caught."""
    fixtures = HERE / "claims" / "fixtures"
    failures: list[str] = []

    good = fixtures / "good"
    good_violations = check_register(_load_yaml(good / "register.yaml"), good)
    if good_violations:
        failures.append(f"good: expected no violations, got {good_violations}")

    for bad in sorted(p for p in fixtures.glob("bad-*") if p.is_dir()):
        expect = (bad / "expect.txt").read_text(encoding="utf-8").strip()
        violations = check_register(_load_yaml(bad / "register.yaml"), bad)
        if not violations:
            failures.append(f"{bad.name}: expected a violation, got none")
        elif not any(expect.lower() in v.lower() for v in violations):
            failures.append(
                f"{bad.name}: no violation contained {expect!r}; got {violations}"
            )

    if failures:
        print("CLAIMS SELF-TEST FAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("CLAIMS SELF-TEST PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    return run_check()


if __name__ == "__main__":
    raise SystemExit(main())
