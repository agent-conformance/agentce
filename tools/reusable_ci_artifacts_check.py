#!/usr/bin/env python3
"""reusable_ci_artifacts_check - the composite Action, the reusable workflow, and the GitLab
component are real, not stubs.

A platform team that wants to gate a build on an AgentCE assessment needs one of three artifacts to
reference: a composite GitHub Action, a ``workflow_call`` reusable workflow, or a GitLab CI/CD
component. This check inspects each artifact's real shape -- ``runs.using: composite``,
``on.workflow_call.inputs``, a two-document GitLab component -- and the surface facts a source-string
grep alone would not distinguish from a declared-but-ignored input: the ``fail-on`` input actually
threaded into an invocation, a ``results.sarif`` upload/artifact, a job/PR summary, and every
third-party action pinned by a full commit SHA.

    reusable_ci_artifacts_check.py              check the repository (the invocation the CI job uses)
    reusable_ci_artifacts_check.py --self-test  prove the checker discriminates on planted good/bad
                                                 fixtures

It imports PyYAML from the ``tools`` project's own environment, so run it there
(``uv run --project tools --frozen python tools/reusable_ci_artifacts_check.py``). No network, no
learned component.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

ACTION_PATH = ROOT / ".github/actions/assess/action.yml"
ACTION_README = ROOT / ".github/actions/assess/README.md"
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-assess.yml"
COMPONENT_PATH = ROOT / ".gitlab/components/agentce-assess/template.yml"

SHA_PIN = re.compile(r"uses:\s*([^\s#]+)")


def _third_party_pins(blob: str) -> list[str]:
    problems: list[str] = []
    for match in SHA_PIN.finditer(blob):
        ref = match.group(1)
        if "/" in ref and "@" in ref and not ref.startswith("./"):
            sha = ref.rsplit("@", 1)[-1]
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                problems.append(f"third-party action {ref!r} is not pinned by a 40-char commit SHA")
    return problems


def check_action(action_path: Path = ACTION_PATH, readme_path: Path = ACTION_README) -> list[str]:
    problems: list[str] = []
    if not action_path.is_file() or action_path.stat().st_size == 0:
        return [f"{action_path}: missing or empty (no composite GitHub Action)"]

    text = action_path.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{action_path}: not valid YAML: {exc}"]

    if not isinstance(doc, dict):
        return [f"{action_path}: top level is not a YAML mapping"]

    runs = doc.get("runs")
    if not isinstance(runs, dict) or runs.get("using") != "composite":
        problems.append(f"{action_path}: runs.using is not 'composite' (got {runs!r})")
    steps = runs.get("steps") if isinstance(runs, dict) else None
    if not isinstance(steps, list) or not steps:
        problems.append(f"{action_path}: runs.steps is missing or empty")

    inputs = doc.get("inputs")
    if not isinstance(inputs, dict) or "fail-on" not in inputs:
        problems.append(f"{action_path}: inputs does not declare a 'fail-on' input")

    if "agentce" not in text or "assess" not in text:
        problems.append(f"{action_path}: no step invokes the real 'agentce assess' CLI surface")
    if "--fail-on" not in text or "inputs.fail-on" not in text:
        problems.append(f"{action_path}: the fail-on input is not wired into a --fail-on invocation")
    if "results.sarif" not in text or "upload-sarif" not in text:
        problems.append(f"{action_path}: no step uploads results.sarif via a SARIF upload action")
    if "GITHUB_STEP_SUMMARY" not in text:
        problems.append(f"{action_path}: no step writes a PR/job summary via GITHUB_STEP_SUMMARY")
    problems += [f"{action_path}: {p}" for p in _third_party_pins(text)]

    if not readme_path.is_file() or readme_path.stat().st_size == 0:
        problems.append(f"{readme_path}: missing or empty (no consumer-pinning documentation)")
    else:
        rtext = readme_path.read_text(encoding="utf-8")
        if re.search(r"uses:\s*\S+@[0-9a-f]{40}", rtext) is None:
            problems.append(f"{readme_path}: no example 'uses: ...@<40-char-sha>' line")
        if "commit sha" not in rtext.lower() and "pin" not in rtext.lower():
            problems.append(f"{readme_path}: does not document pinning by commit SHA")
    return problems


def check_reusable_workflow(wf_path: Path = WORKFLOW_PATH) -> list[str]:
    if not wf_path.is_file() or wf_path.stat().st_size == 0:
        return [f"{wf_path}: missing or empty (no workflow_call reusable workflow)"]

    text = wf_path.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{wf_path}: not valid YAML: {exc}"]
    if not isinstance(doc, dict):
        return [f"{wf_path}: top level is not a YAML mapping"]

    problems: list[str] = []
    # PyYAML follows YAML 1.1 and parses the bare key 'on:' as the boolean True.
    trigger = doc.get("on", doc.get(True))
    if not isinstance(trigger, dict) or "workflow_call" not in trigger:
        problems.append(f"{wf_path}: no on.workflow_call trigger")
    else:
        wc = trigger.get("workflow_call")
        wc_inputs = wc.get("inputs") if isinstance(wc, dict) else None
        if not isinstance(wc_inputs, dict) or "fail-on" not in wc_inputs:
            problems.append(f"{wf_path}: on.workflow_call.inputs does not declare 'fail-on'")

    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        problems.append(f"{wf_path}: no jobs declared")

    if "./.github/actions/assess" not in text and ("agentce" not in text or "assess" not in text):
        problems.append(f"{wf_path}: no job invokes the composite action or the real agentce assess CLI")
    if "inputs.fail-on" not in text:
        problems.append(f"{wf_path}: the workflow_call fail-on input is never referenced by a job")
    if "actions/checkout" in text and not re.search(r"uses:\s*actions/checkout@([0-9a-f]{40})", text):
        problems.append(f"{wf_path}: actions/checkout is referenced but not pinned by a commit SHA")
    problems += [f"{wf_path}: {p}" for p in _third_party_pins(text)]
    return problems


def check_gitlab_component(comp_path: Path = COMPONENT_PATH) -> list[str]:
    if not comp_path.is_file() or comp_path.stat().st_size == 0:
        return [f"{comp_path}: missing or empty (no GitLab CI/CD component)"]

    text = comp_path.read_text(encoding="utf-8")
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        return [f"{comp_path}: not valid YAML: {exc}"]

    if len(docs) < 2:
        return [f"{comp_path}: expected a spec document then a pipeline document; found {len(docs)}"]

    problems: list[str] = []
    spec_doc, pipeline_doc = docs[0], docs[1]
    spec = spec_doc.get("spec") if isinstance(spec_doc, dict) else None
    spec_inputs = spec.get("inputs") if isinstance(spec, dict) else None
    if not isinstance(spec_inputs, dict) or not any("fail" in k for k in spec_inputs):
        problems.append(f"{comp_path}: spec.inputs does not declare a fail-on-like input")
    if not isinstance(pipeline_doc, dict) or not pipeline_doc:
        problems.append(f"{comp_path}: second document has no job definitions")
    if "agentce" not in text or "assess" not in text:
        problems.append(f"{comp_path}: no job invokes the real agentce assess CLI surface")
    if "inputs.fail" not in text:
        problems.append(f"{comp_path}: no job references the fail-on input via $[[ inputs.* ]]")
    if "results.sarif" not in text:
        problems.append(f"{comp_path}: no job exposes results.sarif as an artifact")
    if "report.md" not in text:
        problems.append(f"{comp_path}: no job exposes a human-readable report summary artifact")
    if "artifacts" not in text:
        problems.append(f"{comp_path}: no job declares artifacts:")
    return problems


_GOOD_ACTION = """\
name: agentce-assess
inputs:
  bundle: {required: true}
  fail-on: {required: false, default: ""}
runs:
  using: composite
  steps:
    - shell: bash
      run: |
        fail_on_flag=""
        if [ -n "${{ inputs.fail-on }}" ]; then fail_on_flag="--fail-on ${{ inputs.fail-on }}"; fi
        uv run --project engines/python --frozen agentce assess --bundle "${{ inputs.bundle }}" $fail_on_flag
    - uses: github/codeql-action/upload-sarif@faaca9a8f6edddba5725ffe5adefdab6669a2eca # v3
      with: {sarif_file: out/results.sarif}
    - shell: bash
      run: echo "wrote job summary to $GITHUB_STEP_SUMMARY"
"""
_GOOD_README = """\
Pin this action by its full commit SHA, never a floating tag:

    uses: agent-conformance/agentce/.github/actions/assess@45ca972c172dc8e00a9f1f892c196b5cb884a1d2
"""
_BAD_ACTION_DANGLING = """\
name: agentce-assess
inputs:
  fail-on: {required: false, default: ""}
runs:
  using: composite
  steps:
    - shell: bash
      run: uv run --project engines/python --frozen agentce assess --bundle x
    - uses: github/codeql-action/upload-sarif@v3
      with: {sarif_file: out/results.sarif}
"""

_GOOD_WORKFLOW = """\
on:
  workflow_call:
    inputs:
      fail-on: {required: false, type: string, default: ""}
jobs:
  assess:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - run: |
          uv run --project .agentce-engine/engines/python --frozen agentce assess --fail-on "${{ inputs.fail-on }}"
"""
_BAD_WORKFLOW_DANGLING = """\
on:
  workflow_call:
    inputs:
      fail-on: {required: false, type: string, default: ""}
jobs:
  assess:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run --project .agentce-engine/engines/python --frozen agentce assess
"""

_GOOD_COMPONENT = """\
spec:
  inputs:
    fail_on:
      default: ""
---
agentce-assess:
  script:
    - uv run --project engines/python --frozen agentce assess --fail-on "$[[ inputs.fail_on ]]"
  artifacts:
    paths: [out/results.sarif, out/report.md]
"""
_BAD_COMPONENT_ONE_DOC = """\
spec:
  inputs:
    fail_on:
      default: ""
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def self_test() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        action = base / "action.yml"
        readme = base / "README.md"
        _write(action, _GOOD_ACTION)
        _write(readme, _GOOD_README)
        if check_action(action, readme):
            failures.append(f"good action.yml+README wrongly flagged: {check_action(action, readme)}")
        _write(action, _BAD_ACTION_DANGLING)
        if not check_action(action, readme):
            failures.append("dangling fail-on action.yml wrongly passed")
        _write(action, "")
        if not check_action(action, readme):
            failures.append("empty action.yml wrongly passed")

        wf = base / "reusable-assess.yml"
        _write(wf, _GOOD_WORKFLOW)
        if check_reusable_workflow(wf):
            failures.append(f"good reusable workflow wrongly flagged: {check_reusable_workflow(wf)}")
        _write(wf, _BAD_WORKFLOW_DANGLING)
        if not check_reusable_workflow(wf):
            failures.append("dangling fail-on / unpinned checkout workflow wrongly passed")

        comp = base / "template.yml"
        _write(comp, _GOOD_COMPONENT)
        if check_gitlab_component(comp):
            failures.append(f"good GitLab component wrongly flagged: {check_gitlab_component(comp)}")
        _write(comp, _BAD_COMPONENT_ONE_DOC)
        if not check_gitlab_component(comp):
            failures.append("one-document GitLab component wrongly passed")

        missing = base / "does-not-exist.yml"
        if not check_action(missing, missing):
            failures.append("missing action.yml wrongly passed")
        if not check_reusable_workflow(missing):
            failures.append("missing reusable workflow wrongly passed")
        if not check_gitlab_component(missing):
            failures.append("missing GitLab component wrongly passed")

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("reusable_ci_artifacts_check self-test: fixtures discriminate")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()

    problems = check_action() + check_reusable_workflow() + check_gitlab_component()
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if problems:
        print(f"reusable_ci_artifacts_check: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("reusable_ci_artifacts_check: composite action, reusable workflow, and GitLab component are real")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
