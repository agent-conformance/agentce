"""Repository check: a freshly built artifact runs from an empty directory outside the checkout.

The way a user meets the tool is ``pip install`` / ``npx`` / ``java -jar``, never a checkout. This check
builds each artifact from the current sources, installs it into a clean location under a temporary
directory, and runs it from an empty working directory with the network cut off, so a package that only
works because the monorepo is next to it (or because it reaches out) fails here.

* ``python``: build the wheel and the sdist of ``engines/python``; install each into its own clean venv;
  run ``agentce quickstart``; the canonical outputs must be byte-identical to a run from the checkout.
* ``npm``: build and pack ``engines/typescript``; install the tarball into an empty project; the
  installed ``agentce`` must report the package version, load its dependencies (a ``--json`` envelope),
  and reproduce every published numerics vector.
* ``jar``: build the runnable jar of ``engines/java``; ``java -jar`` must report the package version and
  load its dependencies (a ``--json`` envelope), and the jar must carry the vendored schema and data.

The ``npm`` and ``jar`` checks also run the installed artifact's own ``quickstart`` command — no flags
beyond ``--out`` (kept inside the scratch directory so the run cannot write into the checkout), against
the package's bundled ``corpus/quickstart`` project — and reject a run that answers
``cli.not_implemented`` or that exits 0 having evaluated nothing (the same real-work proof
``tools/assess_smoke_check.py`` gives the in-checkout build).

Network cut-off: inside a network namespace (``unshare -rn``) where the kernel allows it, else dead
proxies plus the offline switches of each tool. ``--require-netns`` makes the namespace mandatory (CI).
Install steps may fetch third-party dependencies; every run step is offline.

* ``records``: build and install the wheel, then time ``agentce assess <folder>`` over a folder of
  OpenTelemetry GenAI exports and one of OpenInference exports, with no other argument: the report must
  evaluate the baseline and the install plus the first report must fit the five-minute budget (Hill 1).
  It is its own kind (the build gate VG-FIRST-REPORT-TIME runs it); ``all`` does not include it.
* ``rerun``: a sender packages and signs a shareable ``corpus/quickstart`` report bundle; a SEPARATE
  recipient installs the wheel fresh and ``verify --report``s it offline, timed from before the
  recipient's own install, reproducing every canonical output byte for byte inside the ten-minute
  budget (Hill 3); a tampered report and a tampered evidence file are each refused with their own exact
  key. It is its own kind (the build gate VG-RERUN-TIME runs it); ``all`` does not include it.
* ``own_rules``: an operator installs fresh, authors a from-scratch catalog (``catalog init``),
  previews its own support matrix (``catalog lint --support-matrix``), signs it with a freshly
  generated key (``catalog sign --new-key --write-trust-root``), and assesses their own evidence
  bundle against it, from an empty directory to a real conformant verdict inside the five-minute
  budget (Hill 6); a catalog tampered after signing is refused at assess time. It is its own kind
  (the build gate VG-OWN-RULES runs it); ``all`` does not include it.

Usage:
    installed_artifacts_check.py {python,records,rerun,own_rules,npm,jar,all} [--offline]
        [--require-netns] [--json]
    installed_artifacts_check.py --self-test
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import assess_smoke_check
import catalog_digest_check
import diff_parity_check
import readiness_parity_check
import sign_parity_check
import verify_parity_check

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
VECTORS = ROOT / "spec" / "rules" / "numerics-vectors" / "cases"
#: The real, signed catalog a quickstart run evaluates (not `baseline`) -- the source for C4's
#: cross-engine catalog-digest and tamper checks (item 18.22).
EU_AI_ACT_DIR = ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
#: `--package-for-sharing`/`sign --write-trust-root`/`verify --report`'s own example and VG-RERUN-TIME's
#: gate input (18.8 D8): the vendored, already-signed corpus/quickstart project, reused as-is.
QUICKSTART_DIR = ROOT / "corpus" / "quickstart"
RERUN_BUDGET_S = 600
DEAD_PROXY = "http://127.0.0.1:9"
# Canonical quickstart outputs. graph.sqlite (a binary store) and manifest.json (carries the run's
# start time) legitimately differ between two runs and are not compared.
CANONICAL_OUTPUTS = (
    "assertions.json",
    "coverage.json",
    "integrity.jsonl",
    "applicability.jsonl",
    "quarantine.jsonl",
    "oscal-ar.json",
    "report.md",
    "report.html",
    "results.sarif",
)


@contextlib.contextmanager
def _scratch(prefix: str) -> Iterator[str]:
    """A temporary directory that is removed even when a namespaced run left root-owned files in it.

    ``tempfile.TemporaryDirectory`` raises from its own error handler in that case.
    """
    path = tempfile.mkdtemp(prefix=prefix)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def package_version() -> str:
    for line in (PY_ENGINE / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in engines/python/pyproject.toml")


def _netns_prefix() -> list[str] | None:
    """The command prefix that runs a program in a namespace with no route off the host, if any."""
    if shutil.which("unshare") is None:
        return None
    if subprocess.run(["unshare", "-rn", "true"], capture_output=True).returncode == 0:
        return ["unshare", "-rn"]
    if shutil.which("sudo") is not None:
        probe = ["sudo", "-n", "unshare", "-n", "true"]
        if subprocess.run(probe, capture_output=True).returncode == 0:
            return ["sudo", "-n", "unshare", "-n"]
    return None


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment: no inherited virtualenv, no checkout on any search path."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "HOME", "TMPDIR", "JAVA_HOME")
    }
    env.update(
        {"LANG": "C", "LC_ALL": "C", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1"}
    )
    env.update(extra or {})
    return env


def _offline_env(env: dict[str, str]) -> dict[str, str]:
    env = dict(env)
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env[name] = DEAD_PROXY
    env.update(
        {
            "NO_PROXY": "",
            "no_proxy": "",
            "UV_OFFLINE": "1",
            "npm_config_offline": "true",
        }
    )
    return env


class Runner:
    def __init__(self, require_netns: bool) -> None:
        self.netns = _netns_prefix()
        if require_netns and self.netns is None:
            raise SystemExit(
                "--require-netns: this host cannot create a network namespace"
            )
        if self.netns is not None:
            probe = self.run(
                [
                    sys.executable,
                    "-c",
                    "import socket; socket.create_connection(('1.1.1.1', 53), 3)",
                ],
                Path(tempfile.gettempdir()),
                offline=True,
            )
            if probe.returncode == 0:
                raise SystemExit("the network namespace can still reach the network")

    def run(
        self,
        cmd: list[str],
        cwd: Path,
        *,
        offline: bool,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = _clean_env(env)
        if offline:
            env = _offline_env(env)
            if self.netns is not None:
                if self.netns[0] == "sudo":
                    # sudo resets the environment; hand ours across explicitly.
                    cmd = [
                        *self.netns[:2],
                        "env",
                        *[f"{k}={v}" for k, v in env.items()],
                        *self.netns[2:],
                        *cmd,
                    ]
                else:
                    cmd = [*self.netns, *cmd]
        return subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
        )


def _fail(step: str, proc: subprocess.CompletedProcess[str]) -> str:
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
    return f"{step}: exit {proc.returncode}: {' | '.join(tail)}"


def _outside_checkout(path: Path) -> bool:
    return ROOT not in path.resolve().parents and path.resolve() != ROOT


def _build_wheel(runner: Runner, tmp: Path, *, offline_install: bool) -> Path | str:
    """Build the Python engine's wheel into ``tmp / "dist"``, returning its path or a `_fail(...)`
    message. Shared by every gate that installs from a fresh wheel; `check_python` builds a wheel
    *and* sdist and has its own block."""
    flags = ["--offline"] if offline_install else []
    proc = runner.run(
        [
            "uv",
            "build",
            *flags,
            "--wheel",
            "--out-dir",
            str(tmp / "dist"),
            str(PY_ENGINE),
        ],
        tmp,
        offline=False,
    )
    wheels = sorted((tmp / "dist").glob("*.whl"))
    if proc.returncode != 0 or len(wheels) != 1:
        return _fail("build the wheel", proc)
    return wheels[0]


def compare_outputs(
    reference: Path, installed: Path, *, may_be_empty: tuple[str, ...] = ()
) -> list[str]:
    """Compare the canonical outputs of an installed run with a checkout run. ``may_be_empty`` names
    outputs that are legitimately empty for the run (for example an empty quarantine)."""
    problems: list[str] = []
    for name in CANONICAL_OUTPUTS:
        ref, got = reference / name, installed / name
        if not got.is_file() or (got.stat().st_size == 0 and name not in may_be_empty):
            problems.append(f"installed run wrote no {name}")
        elif not ref.is_file() or not filecmp.cmp(ref, got, shallow=False):
            problems.append(f"{name} differs from the checkout run")
    ref_packs = sorted(
        p.relative_to(reference) for p in reference.glob("packs/*/pack.json")
    )
    got_packs = sorted(
        p.relative_to(installed) for p in installed.glob("packs/*/pack.json")
    )
    if ref_packs != got_packs or not got_packs:
        problems.append("evidence packs differ from the checkout run")
    else:
        for rel in ref_packs:
            if not filecmp.cmp(reference / rel, installed / rel, shallow=False):
                problems.append(f"{rel} differs from the checkout run")
    for name in ("report.md", "assertions.json"):
        got = installed / name
        if got.is_file() and str(ROOT) in got.read_text(
            encoding="utf-8", errors="replace"
        ):
            problems.append(f"{name} embeds the checkout path")
    return problems


def check_python_artifact(
    runner: Runner,
    artifact: Path,
    tmp: Path,
    reference: Path,
    *,
    offline_install: bool,
    label: str,
) -> list[str]:
    installed = _install(runner, artifact, tmp, label, offline_install=offline_install)
    if isinstance(installed, str):
        return [installed]
    venv, empty = installed
    out = tmp / f"out-{label}"
    proc = runner.run(
        [str(venv / "bin" / "agentce"), "quickstart", "--out", str(out)],
        empty,
        offline=True,
    )
    if proc.returncode != 0:
        return [_fail(f"{label}: agentce quickstart from an empty directory", proc)]
    problems = [f"{label}: {p}" for p in compare_outputs(reference, out)]
    problems += _lens_problems(runner, venv, empty, label)
    exe = [str(venv / "bin" / "agentce")]
    problems += [f"{label}: {p}" for p in _verify_problems(exe, runner, empty)]
    return problems


def _install(
    runner: Runner, artifact: Path, tmp: Path, label: str, *, offline_install: bool
) -> tuple[Path, Path] | str:
    """Install ``artifact`` into a clean venv; return the venv and an empty working directory, or the
    failure text."""
    venv = tmp / f"venv-{label}"
    empty = tmp / f"empty-{label}"
    empty.mkdir()
    proc = runner.run(["uv", "venv", str(venv)], tmp, offline=False)
    if proc.returncode != 0:
        return _fail(f"{label}: create venv", proc)
    flags = ["--offline"] if offline_install else []
    proc = runner.run(
        ["uv", "pip", "install", *flags, str(artifact)],
        tmp,
        offline=False,
        env={"VIRTUAL_ENV": str(venv)},
    )
    if proc.returncode != 0:
        return _fail(f"{label}: install {artifact.name}", proc)
    return venv, empty


def _lens_problems(runner: Runner, venv: Path, empty: Path, label: str) -> list[str]:
    """The installed engine's lens rules, from an empty directory with the network cut: a run that
    names no catalog evaluates the baseline and its report lists every lens; a catalog the profile,
    ``--catalog`` or ``--catalog-dir`` names is evaluated alone; an empty ``--catalog`` is refused."""
    agentce = str(venv / "bin" / "agentce")
    proc = runner.run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "from agentce import bundled; print(bundled.quickstart_dir()); "
            "print(bundled.catalogs_dir() / 'base' / 'eu-ai-act')",
        ],
        empty,
        offline=True,
    )
    if proc.returncode != 0:
        return [_fail(f"{label}: locate the bundled data", proc)]
    quickstart, eu_dir = (Path(line) for line in proc.stdout.split())
    profile = (quickstart / "applicability.yaml").read_text(encoding="utf-8")
    kept: list[str] = []
    in_catalogs = False
    for line in profile.splitlines():
        if line == "catalogs:":
            in_catalogs = True
        elif not (in_catalogs and line.startswith("  - ")):
            in_catalogs = False
            kept.append(line)
    (empty / "no-catalog.yaml").write_text("\n".join(kept) + "\n", encoding="utf-8")
    base = [
        agentce,
        "assess",
        "--bundle",
        str(quickstart / "evidence"),
        "--domain",
        str(quickstart / "domain.linkml.yaml"),
    ]
    cases = [
        ("default", ["--profile", "no-catalog.yaml"], ["baseline@2026.09"]),
        (
            "profile",
            ["--profile", str(quickstart / "applicability.yaml")],
            ["eu-ai-act@2026.09"],
        ),
        (
            "explicit",
            ["--profile", "no-catalog.yaml", "--catalog", "nist-ai-rmf@2026.09"],
            ["nist-ai-rmf@2026.09"],
        ),
        (
            "catalog-dir",
            ["--profile", "no-catalog.yaml", "--catalog-dir", str(eu_dir)],
            ["eu-ai-act@2026.09"],
        ),
    ]
    problems: list[str] = []
    for name, extra, expected in cases:
        out = empty / f"lens-{name}"
        proc = runner.run([*base, *extra, "--out", str(out)], empty, offline=True)
        if proc.returncode not in (0, 1):
            problems.append(_fail(f"{label}: assess ({name} lens)", proc))
            continue
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        got = [f"{c['id']}@{c['version']}" for c in manifest["inputs"]["catalogs"]]
        if got != expected:
            problems.append(
                f"{label}: the {name} run evaluated {got}, expected {expected}"
            )
    default_report = empty / "lens-default" / "report.md"
    report = (
        default_report.read_text(encoding="utf-8") if default_report.is_file() else ""
    )
    for needle in (
        "- Lenses available: ",
        "baseline@2026.09 (default)",
        "eu-ai-act@2026.09",
        "nist-ai-rmf@2026.09",
        "choose one with --catalog <id@version>",
    ):
        if needle not in report:
            problems.append(f"{label}: the default report lacks {needle!r}")
    proc = runner.run(
        [
            *base,
            "--profile",
            "no-catalog.yaml",
            "--catalog",
            ",",
            "--out",
            str(empty / "lens-empty"),
        ],
        empty,
        offline=True,
    )
    if proc.returncode != 3 or "input.catalog_missing" not in proc.stdout + proc.stderr:
        problems.append(
            f"{label}: an empty --catalog was not refused with input.catalog_missing"
        )
    return problems


#: The first report from an installed package, from the start of the install to the report on disk
#: (Hill 1; VG-FIRST-REPORT-TIME).
FIRST_REPORT_BUDGET_S = 300
_OTEL_FIXTURES = ROOT / "adapters" / "otel-genai" / "fixtures"


def _records_folder(directory: Path, fixtures: list[tuple[str, str]]) -> Path:
    """A records folder of the adapter's own fixtures: ``(fixture, file name)`` pairs."""
    directory.mkdir(parents=True)
    for fixture, name in fixtures:
        shutil.copy(_OTEL_FIXTURES / fixture / "input.json", directory / name)
    return directory


def _budget_problem(
    label: str,
    elapsed_s: float,
    budget_s: float,
    *,
    what: str = "install and first report",
) -> str | None:
    if elapsed_s < budget_s:
        return None
    return f"{label}: {what} took {elapsed_s:.0f}s, over the {budget_s:.0f}s budget"


def _expected_events(fixtures: list[tuple[str, str]]) -> int:
    """How many events the adapter's own expected output holds for these fixtures."""
    return sum(
        len(
            (_OTEL_FIXTURES / fixture / "expected.jsonl")
            .read_text("utf-8")
            .splitlines()
        )
        for fixture, _ in fixtures
    )


def _records_problems(
    runner: Runner, venv: Path, empty: Path, label: str, started: float
) -> list[str]:
    """``agentce assess <folder>`` from the installed package, with no other argument, the network cut:
    a folder of OpenTelemetry GenAI exports and a folder of OpenInference exports each exit 0, read
    exactly the events the adapter's expected output lists, evaluate the baseline, write output
    byte-identical to a run from the checkout, and finish inside the first-report budget counted from
    ``started`` (the start of the install)."""
    agentce = str(venv / "bin" / "agentce")
    cases = {
        "otel-genai": [
            ("otel-genai-agent-session", "session.json"),
            ("otel-genai-chat", "chat.json"),
        ],
        "openinference": [("openinference-rag", "rag.json")],
    }
    problems: list[str] = []
    finished = time.monotonic()
    ran: list[tuple[str, Path, Path]] = []
    for name, fixtures in cases.items():
        folder = _records_folder(empty / f"records-{name}", fixtures)
        workdir = empty / f"first-report-{name}"
        workdir.mkdir()
        out = workdir / "out"  # the default output folder: no --out is given
        proc = runner.run(
            [agentce, "assess", str(folder), "--json"], workdir, offline=True
        )
        finished = time.monotonic()
        if proc.returncode != 0:
            problems.append(_fail(f"{label}: assess <folder> ({name} records)", proc))
            continue
        try:
            envelope = json.loads(proc.stdout)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            labels = [
                f"{c['id']}@{c['version']}" for c in manifest["inputs"]["catalogs"]
            ]
        except (OSError, ValueError, KeyError) as exc:
            problems.append(f"{label}: the {name} run left no readable result: {exc}")
            continue
        want = _expected_events(fixtures)
        if envelope.get("accepted") != want or envelope.get("quarantined") != 0:
            problems.append(
                f"{label}: the {name} run accepted {envelope.get('accepted')} events "
                f"({envelope.get('quarantined')} quarantined), expected {want}"
            )
        if labels != ["baseline@2026.09"]:
            problems.append(
                f"{label}: the {name} run evaluated {labels}, not the baseline"
            )
        if not (out / "applicability.yaml").is_file():
            problems.append(f"{label}: the {name} run wrote no default profile")
        ran.append((name, folder, out))
    # The reference runs come after the timed installed runs, so they never count against the budget.
    for name, folder, out in ran:
        reference = empty / f"reference-{name}"
        ref_proc = runner.run(
            [
                "uv",
                "run",
                "--frozen",
                "--project",
                str(PY_ENGINE),
                "agentce",
                "assess",
                str(folder),
                "--out",
                str(reference),
            ],
            empty,
            offline=False,
        )
        if ref_proc.returncode != 0:
            problems.append(
                _fail(f"{label}: reference {name} run from the checkout", ref_proc)
            )
            continue
        problems += [
            f"{label}: {name}: {p}"
            for p in compare_outputs(reference, out, may_be_empty=("quarantine.jsonl",))
        ]
    budget = _budget_problem(label, finished - started, FIRST_REPORT_BUDGET_S)
    return problems + ([budget] if budget else [])


def check_records(runner: Runner, *, offline_install: bool) -> list[str]:
    """Build the wheel, install it, and time the first report from a records folder."""
    with _scratch("agentce-first-report-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        wheel = _build_wheel(runner, tmp, offline_install=offline_install)
        if isinstance(wheel, str):
            return [wheel]
        started = time.monotonic()
        installed = _install(
            runner, wheel, tmp, "wheel", offline_install=offline_install
        )
        if isinstance(installed, str):
            return [installed]
        venv, empty = installed
        return _records_problems(runner, venv, empty, "wheel", started)


def _fixture_agent_id(fixture: str) -> str:
    """The real agent id the adapter's own expected output names for ``fixture`` -- read the same way
    :func:`_expected_events` reads an event count, so this check and the fixture it drives can never
    drift apart by hand."""
    first = json.loads(
        (_OTEL_FIXTURES / fixture / "expected.jsonl").read_text("utf-8").splitlines()[0]
    )
    return str(first["data"]["agent"]["id"])


#: The two real agent ids the `datadog`/`langfuse` fixtures name (18.14, VG-PROJECT-TIME) -- the same
#: fixture combination `test_records_assess.py`'s own `_multi_agent_records` helper uses, read from
#: the fixtures themselves rather than duplicated by hand.
_PROJECT_REAL_AGENTS = frozenset(
    {_fixture_agent_id("datadog"), _fixture_agent_id("langfuse")}
)


def _project_problems(
    runner: Runner, venv: Path, empty: Path, label: str, started: float
) -> list[str]:
    """``agentce assess <folder> --for risk-lead`` from the installed package, offline, over a
    records folder naming two distinct agents plus one file with no agent id at all, and NO
    ``--profile`` (so every discovered agent is genuinely undeclared, Hill 7's own scenario): exits 0,
    writes ``project.md``/``project.json`` listing all three discovered subjects (the two real agent
    ids plus the id-less ``DEFAULT_SUBJECT`` catch-all), ``undeclared_agents`` names exactly the two
    real agent ids, each real agent's own ``agents/<dirname>/assertions.json`` names only that agent's
    own findings, and the whole run finishes inside the first-report budget counted from ``started``."""
    agentce = str(venv / "bin" / "agentce")
    fixtures = [
        ("datadog", "fraud.json"),
        ("langfuse", "checkout.json"),
        ("openinference-rag", "rag.json"),
    ]
    folder = _records_folder(empty / "records-project", fixtures)
    workdir = empty / "project"
    workdir.mkdir()
    out = workdir / "out"
    proc = runner.run(
        [agentce, "assess", str(folder), "--for", "risk-lead", "--json"],
        workdir,
        offline=True,
    )
    finished = time.monotonic()
    problems: list[str] = []
    if proc.returncode != 0:
        problems.append(_fail(f"{label}: assess <folder> --for risk-lead", proc))
    else:
        try:
            project = json.loads((out / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(
                f"{label}: the project run left no readable project.json: {exc}"
            )
            project = None
        if project is not None:
            subject_ids = {row["id"] for row in project.get("agents", [])}
            if (
                len(subject_ids) != len(_PROJECT_REAL_AGENTS) + 1
                or not _PROJECT_REAL_AGENTS <= subject_ids
            ):
                problems.append(
                    f"{label}: project.json lists {sorted(subject_ids)}, "
                    "not the two real agents plus the catch-all"
                )
            if set(project.get("undeclared_agents", [])) != _PROJECT_REAL_AGENTS:
                problems.append(
                    f"{label}: undeclared_agents is {project.get('undeclared_agents')}, "
                    "not exactly the two real agent ids"
                )
            agents_dir = out / "agents"
            if not agents_dir.is_dir():
                problems.append(f"{label}: the project run wrote no agents/ directory")
            else:
                owners: set[str] = set()
                for entry in sorted(agents_dir.iterdir()):
                    try:
                        own = json.loads(
                            (entry / "assertions.json").read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError) as exc:
                        problems.append(
                            f"{label}: {entry.name}/assertions.json unreadable: {exc}"
                        )
                        continue
                    subjects = {a["subject"] for a in own}
                    if len(subjects) != 1:
                        problems.append(
                            f"{label}: {entry.name}/assertions.json names "
                            f"{sorted(subjects)}, not exactly one subject"
                        )
                        continue
                    owners.update(subjects)
                missing = _PROJECT_REAL_AGENTS - owners
                if missing:
                    problems.append(
                        f"{label}: no own report directory for {sorted(missing)}"
                    )
    budget = _budget_problem(label, finished - started, FIRST_REPORT_BUDGET_S)
    return problems + ([budget] if budget else [])


def check_project(runner: Runner, *, offline_install: bool) -> list[str]:
    """Build the wheel, install it, and time the risk-lead project view from a multi-agent records
    folder, with no --profile (Hill 7)."""
    with _scratch("agentce-project-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        wheel = _build_wheel(runner, tmp, offline_install=offline_install)
        if isinstance(wheel, str):
            return [wheel]
        started = time.monotonic()
        installed = _install(
            runner, wheel, tmp, "wheel", offline_install=offline_install
        )
        if isinstance(installed, str):
            return [installed]
        venv, empty = installed
        return _project_problems(runner, venv, empty, "wheel", started)


def _generate_kms_key(runner: Runner, venv: Path, tmp: Path, name: str) -> Path:
    """A throwaway Ed25519 test key (never committed), written with the venv's own ``cryptography``
    (a transitive dependency of the installed package -- this project takes no direct dependency on
    it)."""
    key_path = tmp / f"{name}.pem"
    proc = runner.run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey\n"
            "from cryptography.hazmat.primitives.serialization import (\n"
            "    Encoding, NoEncryption, PrivateFormat)\n"
            "import sys\n"
            "key = Ed25519PrivateKey.generate()\n"
            "open(sys.argv[1], 'wb').write(\n"
            "    key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))\n",
            str(key_path),
        ],
        tmp,
        offline=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(_fail(f"{name}: generate test key", proc))
    return key_path


def _assert_tamper_refused(
    runner: Runner,
    recipient_agentce: str,
    recipient_empty: Path,
    sender_out: Path,
    trust_root_copy: Path,
    *,
    dest_name: str,
    corrupt: Callable[[Path], Path],
    expected_key: str,
) -> str | None:
    """Copy `sender_out` to `recipient_empty/dest_name`, corrupt the file `corrupt` names, and assert
    the recipient's `verify --report` refuses it with `expected_key` (a TEETH case)."""
    dest = recipient_empty / dest_name
    shutil.copytree(sender_out, dest)
    target = corrupt(dest)
    target.write_bytes(target.read_bytes() + b"TAMPER")
    proc = runner.run(
        [
            recipient_agentce,
            "verify",
            "--report",
            str(dest),
            "--signer-trust-root",
            str(trust_root_copy),
            "--json",
        ],
        recipient_empty,
        offline=True,
    )
    if proc.returncode != 3 or expected_key not in proc.stdout:
        return _fail(
            f"TEETH: a tampered {dest_name} was not refused as {expected_key}", proc
        )
    return None


def check_rerun(runner: Runner, *, offline_install: bool) -> list[str]:
    """VG-RERUN-TIME (Hill 3, contracts/P18-18.8.md C4): a sender packages and signs a shareable
    report bundle from `corpus/quickstart`; a SEPARATE recipient installs the engine fresh and
    `verify --report`s it offline, reproducing every canonical output byte for byte inside the timed
    budget (D10: the clock starts before the recipient's own install, never the sender's); a tampered
    copy of the report and a tampered copy of its evidence are each refused with their own exact
    key."""
    with _scratch("agentce-rerun-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        wheel = _build_wheel(runner, tmp, offline_install=offline_install)
        if isinstance(wheel, str):
            return [wheel]

        # 1. Sender venv (untimed): package and sign corpus/quickstart for sharing.
        sender_installed = _install(
            runner, wheel, tmp, "sender", offline_install=offline_install
        )
        if isinstance(sender_installed, str):
            return [sender_installed]
        sender_venv, sender_empty = sender_installed
        sender_agentce = str(sender_venv / "bin" / "agentce")
        sender_out = sender_empty / "sender-out"
        proc = runner.run(
            [
                sender_agentce,
                "assess",
                "--bundle",
                str(QUICKSTART_DIR / "evidence"),
                "--profile",
                str(QUICKSTART_DIR / "applicability.yaml"),
                "--domain",
                str(QUICKSTART_DIR / "domain.linkml.yaml"),
                "--package-for-sharing",
                "--out",
                str(sender_out),
            ],
            sender_empty,
            offline=True,
        )
        if proc.returncode != 0:
            return [_fail("sender: assess --package-for-sharing", proc)]
        try:
            key_path = _generate_kms_key(runner, sender_venv, tmp, "sender-key")
        except RuntimeError as exc:
            return [str(exc)]
        proc = runner.run(
            [
                sender_agentce,
                "sign",
                str(sender_out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key_path),
                "--write-trust-root",
            ],
            sender_empty,
            offline=True,
        )
        if proc.returncode != 0:
            return [_fail("sender: sign --write-trust-root", proc)]
        trust_root_copy = tmp / "sender-trust-root.json"
        trust_root_copy.write_bytes((sender_out / "trust-root.json").read_bytes())

        # 2. A SEPARATE recipient venv; the timed window opens before ITS install (D10), never
        # counting the sender's own build/install/sign above.
        started = time.monotonic()
        recipient_installed = _install(
            runner, wheel, tmp, "recipient", offline_install=offline_install
        )
        if isinstance(recipient_installed, str):
            return [recipient_installed]
        recipient_venv, recipient_empty = recipient_installed
        recipient_agentce = str(recipient_venv / "bin" / "agentce")

        proc = runner.run(
            [recipient_agentce, "verify", "--report", str(sender_out), "--json"],
            recipient_empty,
            offline=True,
        )
        elapsed = time.monotonic() - started
        problems: list[str] = []
        if proc.returncode != 0:
            problems.append(_fail("recipient: verify --report", proc))
        else:
            try:
                envelope = json.loads(proc.stdout)
            except ValueError as exc:
                problems.append(
                    f"recipient: verify --report printed no readable JSON: {exc}"
                )
                envelope = {}
            if envelope.get("reproduced") is not True:
                problems.append(
                    "recipient: verify --report reported "
                    f"reproduced={envelope.get('reproduced')!r}, not true"
                )
        budget = _budget_problem(
            "recipient", elapsed, RERUN_BUDGET_S, what="install and re-run"
        )
        if budget:
            problems.append(budget)

        # 3. TEETH, both against the RECIPIENT install, both with the trust root kept EXTERNAL (D6's
        # embedded-trust exception is out of scope for a tamper case) so the check under test is the
        # real one.
        report_problem = _assert_tamper_refused(
            runner,
            recipient_agentce,
            recipient_empty,
            sender_out,
            trust_root_copy,
            dest_name="tampered-report-out",
            corrupt=lambda dest: dest / "report.md",
            expected_key="report_output_tampered",
        )
        if report_problem:
            problems.append(report_problem)

        evidence_problem = _assert_tamper_refused(
            runner,
            recipient_agentce,
            recipient_empty,
            sender_out,
            trust_root_copy,
            dest_name="tampered-evidence-out",
            corrupt=lambda dest: next(
                (dest / "bundle" / "evidence" / "events").glob("*.jsonl")
            ),
            expected_key="report_evidence_tampered",
        )
        if evidence_problem:
            problems.append(evidence_problem)
        return problems


#: The catalog-authoring walkthrough's own budget (Hill 6, VG-OWN-RULES): the same "install to first
#: report" shape as VG-FIRST-REPORT-TIME, now against a self-authored catalog.
OWN_RULES_BUDGET_S = FIRST_REPORT_BUDGET_S
#: The scaffold's one requirement (Decision/self_report) has no adapter producer at all: a true,
#: meaningful "only your own code can emit this" answer (contracts/P18-18.9.md C4/C5).
_OWN_RULES_REQUIREMENT = {
    "event": "Decision",
    "class": "self_report",
    "ladder_rung": 2,
    "owner_key": "agent_team",
    "step_kind": "code_change",
    "supplying_adapters": [],
}


def _own_rules_assess_cmd(
    agentce: str, directory: Path, trust_root: Path, out: Path
) -> list[str]:
    return [
        agentce,
        "assess",
        "--bundle",
        str(QUICKSTART_DIR / "evidence"),
        "--profile",
        str(QUICKSTART_DIR / "applicability.yaml"),
        "--domain",
        str(QUICKSTART_DIR / "domain.linkml.yaml"),
        "--catalog-dir",
        str(directory),
        "--trust-root",
        str(trust_root),
        "--out",
        str(out),
        "--emit",
        "md",
        "--json",
    ]


def check_own_rules(runner: Runner, *, offline_install: bool) -> list[str]:
    """VG-OWN-RULES (Hill 6, contracts/P18-18.9.md C5): an operator installs fresh, authors a
    from-scratch catalog (`catalog init`), previews its own support matrix (`catalog lint
    --support-matrix`), signs it with a freshly generated key (`catalog sign --new-key
    --write-trust-root`), and assesses their own evidence bundle against it, from an empty directory
    to a real conformant verdict within the timed budget (D10: the clock starts at the operator's own
    install, matching VG-FIRST-REPORT-TIME); a catalog tampered after signing is refused at assess
    time."""
    with _scratch("agentce-own-rules-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        wheel = _build_wheel(runner, tmp, offline_install=offline_install)
        if isinstance(wheel, str):
            return [wheel]

        started = time.monotonic()
        installed = _install(
            runner, wheel, tmp, "operator", offline_install=offline_install
        )
        if isinstance(installed, str):
            return [installed]
        venv, empty = installed
        agentce = str(venv / "bin" / "agentce")
        directory = empty / "own-rules-cat"

        proc = runner.run(
            [agentce, "catalog", "init", str(directory), "--json"], empty, offline=True
        )
        if proc.returncode != 0:
            return [_fail("operator: catalog init", proc)]
        try:
            family = str(json.loads(proc.stdout)["family"])
        except (ValueError, KeyError) as exc:
            return [f"operator: catalog init printed no readable family: {exc}"]

        # An overlay-only family (CND, from overlays/eu-ai-act-annex-iii) is invisible to a
        # `spec/`-based implementation that only walks the three base catalogs; only the installed
        # wheel's own `bundled.vendored_catalogs()` proves the collision check sees it too (contracts/
        # P18-18.9.md C1, round 2's negative case).
        overlay_collision_dir = empty / "overlay-collision-cat"
        proc = runner.run(
            [
                agentce,
                "catalog",
                "init",
                str(overlay_collision_dir),
                "--id",
                "CND",
                "--json",
            ],
            empty,
            offline=True,
        )
        if proc.returncode != 3 or "catalog.init_family_collision" not in proc.stdout:
            return [
                _fail(
                    "operator: catalog init --id CND (an overlay family) was not "
                    "refused as catalog.init_family_collision",
                    proc,
                )
            ]
        if overlay_collision_dir.exists():
            return ["operator: catalog init --id CND wrote output despite refusing"]

        support_matrix = empty / "support-matrix.json"
        proc = runner.run(
            [
                agentce,
                "catalog",
                "lint",
                str(directory),
                "--support-matrix",
                str(support_matrix),
                "--json",
            ],
            empty,
            offline=True,
        )
        if proc.returncode != 0:
            return [_fail("operator: catalog lint --support-matrix", proc)]
        problems: list[str] = []
        try:
            matrix = json.loads(support_matrix.read_text("utf-8"))["controls"]
            entry = next(e for e in matrix if e["control"] == f"{family}-01")
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            return [
                f"operator: no readable support matrix entry for {family}-01: {exc}"
            ]
        if entry["mode"] != "automated" or entry["rung"] != 2:
            problems.append(
                f"operator: {family}-01's support matrix entry is "
                f"mode={entry['mode']!r} rung={entry['rung']!r}, not automated/2"
            )
        if entry["requirements"] != [_OWN_RULES_REQUIREMENT]:
            problems.append(
                f"operator: {family}-01's support matrix requirement is "
                f"{entry['requirements']!r}, not the pinned literal"
            )

        key_path = empty / "own-rules-key.pem"
        trust_root = empty / "own-rules-trust.json"
        proc = runner.run(
            [
                agentce,
                "catalog",
                "sign",
                str(directory),
                "--new-key",
                str(key_path),
                "--write-trust-root",
                str(trust_root),
                "--json",
            ],
            empty,
            offline=True,
        )
        if proc.returncode != 0:
            return problems + [_fail("operator: catalog sign --new-key", proc)]

        out = empty / "report"
        assess_cmd = _own_rules_assess_cmd(agentce, directory, trust_root, out)
        proc = runner.run(assess_cmd, empty, offline=True)
        finished = time.monotonic()
        if proc.returncode != 0:
            return problems + [_fail("operator: assess --catalog-dir", proc)]
        try:
            assertions = json.loads((out / "assertions.json").read_text("utf-8"))
            manifest = json.loads((out / "manifest.json").read_text("utf-8"))
        except (OSError, ValueError) as exc:
            return problems + [f"operator: assess left no readable output: {exc}"]
        matches = [
            a
            for a in assertions
            if a["control"] == f"{family}-01"
            and a["subject"] == "spiffe://corp/agents/credit-langgraph"
        ]
        if len(matches) != 1 or matches[0]["outcome"] != "conformant":
            problems.append(
                f"operator: assess did not reach a conformant {family}-01 "
                f"verdict for the quickstart subject: {matches!r}"
            )
        if "limitations" in manifest:
            problems.append(
                "operator: assess reported limitations on a fully verified run"
            )
        catalog_ids = [c["id"] for c in manifest["inputs"]["catalogs"]]
        if "own-rules-cat" not in catalog_ids:
            problems.append(
                f"operator: assess's manifest names catalogs {catalog_ids!r}, "
                "not own-rules-cat"
            )
        budget = _budget_problem(
            "operator",
            finished - started,
            OWN_RULES_BUDGET_S,
            what="install, author, sign, and assess",
        )
        if budget:
            problems.append(budget)

        # TEETH: a catalog tampered AFTER signing is refused at assess time. Verification happens
        # before any output is written (cmd_assess resolves and verifies catalogs before touching
        # `--out`), so re-running the SAME command against the still-present `report` directory from
        # the successful run above is safe: a refusal never touches it. The append itself runs
        # through `runner.run` (the venv's own python, not this process's) so it holds the same
        # privilege as whatever created the file: under CI's network namespace, `catalog init` runs
        # as root via `sudo unshare`, and this process cannot write that file directly.
        control_file = directory / "controls" / f"{family}-01.yaml"
        proc = runner.run(
            [
                str(venv / "bin" / "python"),
                "-c",
                "import pathlib, sys\n"
                "p = pathlib.Path(sys.argv[1])\n"
                "p.write_text(p.read_text('utf-8') + '# tampered\\n', encoding='utf-8')\n",
                str(control_file),
            ],
            empty,
            offline=True,
        )
        if proc.returncode != 0:
            return problems + [_fail("operator: append the tamper byte", proc)]
        proc = runner.run(assess_cmd, empty, offline=True)
        if proc.returncode != 3 or "input.catalog_unverified" not in proc.stdout:
            problems.append(
                _fail(
                    "TEETH: a catalog tampered after signing was not refused as "
                    "input.catalog_unverified",
                    proc,
                )
            )
        return problems


def check_python(runner: Runner, *, offline_install: bool) -> list[str]:
    with _scratch("agentce-installed-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        flags = ["--offline"] if offline_install else []
        proc = runner.run(
            ["uv", "build", *flags, "--out-dir", str(tmp / "dist"), str(PY_ENGINE)],
            tmp,
            offline=False,
        )
        if proc.returncode != 0:
            return [_fail("build the wheel and sdist", proc)]
        wheels, sdists = (
            sorted((tmp / "dist").glob("*.whl")),
            sorted((tmp / "dist").glob("*.tar.gz")),
        )
        if len(wheels) != 1 or len(sdists) != 1:
            return [
                f"expected one wheel and one sdist, built {len(wheels)} and {len(sdists)}"
            ]
        return check_python_files(
            runner,
            [("wheel", wheels[0]), ("sdist", sdists[0])],
            offline_install=offline_install,
        )


def check_python_files(
    runner: Runner, artifacts: list[tuple[str, Path]], *, offline_install: bool
) -> list[str]:
    """Install each built or downloaded artifact into a clean venv and compare with a checkout run."""
    with _scratch("agentce-installed-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        reference = tmp / "reference"
        proc = runner.run(
            [
                "uv",
                "run",
                "--frozen",
                "--project",
                str(PY_ENGINE),
                "agentce",
                "quickstart",
                "--out",
                str(reference),
            ],
            tmp,
            offline=False,
        )
        if proc.returncode != 0:
            return [_fail("reference quickstart from the checkout", proc)]
        problems: list[str] = []
        for label, artifact in artifacts:
            problems += check_python_artifact(
                runner,
                artifact,
                tmp,
                reference,
                offline_install=offline_install,
                label=label,
            )
        return problems


# --- version --json, real catalog digests, and the Verdict section, the way a user meets them in an
# installed TypeScript or Java artifact (item 18.22) -- pure functions so the self-test can prove each
# one discriminates a broken fixture without a full install/build cycle. --------------------------


def _python_reference_quickstart(runner: Runner, tmp: Path) -> Path | str:
    """A real Python-engine quickstart run from the checkout: the cross-engine reference every other
    engine's rendered Verdict section must match byte-for-byte (C1(h)/C4)."""
    reference = tmp / "py-reference"
    proc = runner.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "quickstart",
            "--out",
            str(reference),
        ],
        tmp,
        offline=False,
    )
    if proc.returncode != 0:
        return _fail("python reference quickstart", proc)
    return reference


def _python_spec_version(reference: Path) -> str | None:
    """The Python engine's own `spec_version`, for the `version --json` cross-engine assertion --
    read off the reference quickstart's own `manifest.json` (`engine.spec_version`, written by
    `build_manifest`) rather than launching a second `uv run` process just to ask the engine again."""
    manifest_path = reference / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    version: str | None = manifest.get("engine", {}).get("spec_version")
    return version


def _version_plain_problems(
    stdout: str, returncode: int, engine_id: str, version: str
) -> list[str]:
    """The two-line plain-text form of `version` (no `--json`): `<engine> <version> (spec <spec>)`
    then `no_ml: <result>`, matching Python's own `cmd_version` output field for field."""
    problems: list[str] = []
    if returncode != 0:
        problems.append(f"version: exit {returncode}, expected 0 for a passing no_ml")
    lines = stdout.strip("\n").splitlines()
    expected_first = f"{engine_id} {version} (spec "
    if len(lines) != 2 or not lines[0].startswith(expected_first):
        problems.append(
            f"version: printed {stdout!r}, expected two lines starting {expected_first!r}"
        )
    elif not lines[1].startswith("no_ml: "):
        problems.append(
            f"version: second line {lines[1]!r} does not start with 'no_ml: '"
        )
    return problems


def _version_json_problems(
    stdout: str, returncode: int, engine_id: str, spec_version: str
) -> list[str]:
    """`version --json`'s structured envelope: the same shape `validate`/`assess` already produce,
    with a real installed-artifact `no_ml` self-report (never a literal `pass`)."""
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return [f"version --json: output is not JSON: {stdout.strip()[:200]!r}"]
    problems: list[str] = []
    if envelope.get("engine") != engine_id:
        problems.append(
            f"version --json: engine={envelope.get('engine')!r}, expected {engine_id!r}"
        )
    if envelope.get("spec_version") != spec_version:
        problems.append(
            f"version --json: spec_version={envelope.get('spec_version')!r}, "
            f"expected {spec_version!r} (the Python engine's own)"
        )
    if envelope.get("no_ml") != "pass":
        problems.append(
            f"version --json: no_ml={envelope.get('no_ml')!r}, expected 'pass' for the real "
            "installed artifact's own (non-denylisted) bundled dependencies"
        )
    detail = envelope.get("no_ml_detail")
    if not isinstance(detail, dict) or "denylisted_present" not in detail:
        problems.append(
            "version --json: no_ml_detail is missing or lacks denylisted_present"
        )
    if returncode != 0:
        problems.append(
            f"version --json: exit {returncode}, expected 0 for a passing no_ml"
        )
    return problems


def _version_problems(
    exe: list[str], runner: Runner, cwd: Path, engine_id: str, spec_version: str
) -> list[str]:
    version = package_version()
    proc = runner.run([*exe, "version"], cwd, offline=True)
    problems = _version_plain_problems(proc.stdout, proc.returncode, engine_id, version)
    proc = runner.run([*exe, "version", "--json"], cwd, offline=True)
    problems += _version_json_problems(
        proc.stdout, proc.returncode, engine_id, spec_version
    )
    return problems


def _diff_json_problems(
    stdout: str,
    returncode: int,
    expected_keys: set[tuple[str, str]],
) -> list[str]:
    """A `diff --json` envelope must carry the real content-keyed delta, not merely exit
    non-zero for some unrelated reason -- a `diff` that always answers "no differences" (item
    18.24's own named seeded fault) is caught here by its `diff` array's shape, and separately
    by the exit code being wrong whenever that array is non-empty."""
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return [f"diff --json: output is not JSON: {stdout.strip()[:200]!r}"]
    diff = envelope.get("diff")
    if not isinstance(diff, list):
        return ["diff --json: envelope has no 'diff' array"]
    problems: list[str] = []
    if len(diff) != len(expected_keys):
        problems.append(
            f"diff --json: diff array has {len(diff)} entries, expected {len(expected_keys)}"
        )
    got_keys = {
        (e.get("control"), e.get("subject")) for e in diff if isinstance(e, dict)
    }
    if got_keys != expected_keys:
        problems.append(
            f"diff --json: diff array keys {sorted(got_keys)!r}, "
            f"expected {sorted(expected_keys)!r}"
        )
    if diff and returncode != 1:
        problems.append(
            f"diff --json: exit {returncode} on a real differing pair, expected 1"
        )
    return problems


def _diff_md_problems(stdout: str) -> list[str]:
    problems: list[str] = []
    if "## What changed" not in stdout:
        problems.append("diff --format md: missing the '## What changed' heading")
    if "\n### " not in stdout:
        problems.append(
            "diff --format md: missing a '### ' subsection (closed/opened/other)"
        )
    return problems


def _diff_missing_file_problems(stdout: str, returncode: int) -> list[str]:
    problems: list[str] = []
    if returncode != 3:
        problems.append(f"diff missing-file: exit {returncode}, expected 3")
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return problems + [
            f"diff missing-file: output is not JSON: {stdout.strip()[:200]!r}"
        ]
    key = envelope.get("error", {}).get("message_key")
    if key != "input.report_b_missing":
        problems.append(
            f"diff missing-file: error message_key={key!r}, expected "
            "'input.report_b_missing'"
        )
    return problems


def _diff_problems(exe: list[str], runner: Runner, cwd: Path) -> list[str]:
    """`agentce diff <a> <b>` against the same fixture pair `tools/diff_parity_check.py`'s
    cross-engine check uses (item 18.24's C3), run against the installed artifact the way a user
    reaches it (C4): `--json`'s envelope, `--format md`'s rendering, the identical-pair case (exit
    0), and the missing-second-file error case."""
    fixture_dir = cwd / "diff-fixture"
    fixture_dir.mkdir(exist_ok=True)
    a_path, b_path = diff_parity_check.build_fixture(fixture_dir)
    a_entries = json.loads(a_path.read_text(encoding="utf-8"))
    b_entries = json.loads(b_path.read_text(encoding="utf-8"))
    groups = diff_parity_check.what_changed_groups(a_entries, b_entries)
    expected_keys = {entry[0] for group in groups.values() for entry in group}

    problems: list[str] = []
    proc = runner.run(
        [*exe, "diff", str(a_path), str(b_path), "--json"], cwd, offline=True
    )
    problems += _diff_json_problems(proc.stdout, proc.returncode, expected_keys)

    proc = runner.run(
        [*exe, "diff", str(a_path), str(b_path), "--format", "md"], cwd, offline=True
    )
    problems += _diff_md_problems(proc.stdout)

    proc = runner.run([*exe, "diff", str(a_path), str(a_path)], cwd, offline=True)
    if proc.returncode != 0:
        problems.append(f"diff identical-pair: exit {proc.returncode}, expected 0")

    proc = runner.run([*exe, "diff", str(a_path), "--json"], cwd, offline=True)
    problems += _diff_missing_file_problems(proc.stdout, proc.returncode)
    return problems


def _readiness_json_problems(
    stdout: str, returncode: int, expected_verdict: str, expected_exit: int
) -> list[str]:
    """A `readiness --json` envelope must carry the real computed verdict and a real, readable
    `report-readiness-*.md` file on disk -- a `readiness` that always answers READY (item 18.25's own
    named seeded fault) is caught here on scenario-3's own NOT_READY fixture."""
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return [f"readiness --json: output is not JSON: {stdout.strip()[:200]!r}"]
    problems: list[str] = []
    verdict = envelope.get("verdict")
    if verdict != expected_verdict:
        problems.append(
            f"readiness --json: verdict={verdict!r}, expected {expected_verdict!r}"
        )
    if returncode != expected_exit:
        problems.append(
            f"readiness --json: exit {returncode}, expected {expected_exit}"
        )
    report_path = envelope.get("report")
    report = Path(report_path) if report_path else None
    if report is None or not report.is_file():
        problems.append(
            f"readiness --json: report file {report_path!r} does not exist on disk"
        )
        return problems
    lines = report.read_text(encoding="utf-8").splitlines()
    expected_first_line = f"# Report readiness — {expected_verdict}"
    if not lines or lines[0] != expected_first_line:
        problems.append(
            f"readiness --json: report file's first line {lines[0] if lines else ''!r}, "
            f"expected {expected_first_line!r}"
        )
    return problems


def _readiness_missing_report_dir_problems(stdout: str, returncode: int) -> list[str]:
    problems: list[str] = []
    if returncode != 3:
        problems.append(f"readiness missing report_dir: exit {returncode}, expected 3")
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return problems + [
            f"readiness missing report_dir: output is not JSON: {stdout.strip()[:200]!r}"
        ]
    key = envelope.get("error", {}).get("message_key")
    if key != "input.report_dir_missing":
        problems.append(
            f"readiness missing report_dir: error message_key={key!r}, expected "
            "'input.report_dir_missing'"
        )
    return problems


def _readiness_problems(exe: list[str], runner: Runner, cwd: Path) -> list[str]:
    """`agentce readiness <report-dir> --catalog-dir <dir>` against `tools/readiness_parity_check.py`'s
    own scenario-1 (clean, READY) and scenario-3 (NOT_READY, no `--gaps`) fixtures (item 18.25's C3),
    run against the installed artifact the way a user reaches it (C4): a missing `report_dir`
    positional must return `input.report_dir_missing`, not a crash."""
    fixture_dir = cwd / "readiness-fixture"
    fixture_dir.mkdir(exist_ok=True)
    catalog_dir = readiness_parity_check.CATALOG_DIR
    scenarios = {s.name: s for s in readiness_parity_check.SCENARIOS}

    problems: list[str] = []
    report1, extra1 = scenarios["1-clean"].build(fixture_dir)
    proc = runner.run(
        [
            *exe,
            "readiness",
            str(report1),
            *extra1,
            "--catalog-dir",
            str(catalog_dir),
            "--json",
        ],
        cwd,
        offline=True,
    )
    problems += [
        f"scenario-1: {p}"
        for p in _readiness_json_problems(
            proc.stdout, proc.returncode, readiness_parity_check.READY, 0
        )
    ]

    report3, extra3 = scenarios["3-insufficient-evidence-no-gaps"].build(fixture_dir)
    proc = runner.run(
        [
            *exe,
            "readiness",
            str(report3),
            *extra3,
            "--catalog-dir",
            str(catalog_dir),
            "--json",
        ],
        cwd,
        offline=True,
    )
    problems += [
        f"scenario-3: {p}"
        for p in _readiness_json_problems(
            proc.stdout, proc.returncode, readiness_parity_check.NOT_READY, 1
        )
    ]

    proc = runner.run([*exe, "readiness", "--json"], cwd, offline=True)
    problems += _readiness_missing_report_dir_problems(proc.stdout, proc.returncode)
    return problems


def _sign_ready_json_problems(
    stdout: str, returncode: int, expected_keyid: str
) -> list[str]:
    """A `sign --json` envelope over a READY report must actually sign it: exit 0, the reported
    `keyid` matching the known test key, and a real, readable detached signature file on disk whose
    content is a DSSE envelope over an in-toto Statement (item 18.26's own C4) -- a `sign` that
    signs an unready report the same way as a ready one (the contract's own named seeded fault, "a
    sign implementation that writes a signature without checking readiness first") is caught by
    `_sign_not_ready_json_problems` below, not here."""
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return [f"sign --json: output is not JSON: {stdout.strip()[:200]!r}"]
    problems: list[str] = []
    if returncode != 0:
        problems.append(f"sign --json: exit {returncode}, expected 0")
    keyid = envelope.get("keyid")
    if keyid != expected_keyid:
        problems.append(f"sign --json: keyid={keyid!r}, expected {expected_keyid!r}")
    sig_path = envelope.get("signature")
    sig = Path(sig_path) if sig_path else None
    if sig is None or not sig.is_file():
        problems.append(
            f"sign --json: signature file {sig_path!r} does not exist on disk"
        )
        return problems
    try:
        detached = json.loads(sig.read_text(encoding="utf-8"))
    except ValueError:
        problems.append(f"sign --json: signature file {sig_path!r} is not valid JSON")
        return problems
    if detached.get("payloadType") != "application/vnd.in-toto+json":
        problems.append(
            f"sign --json: signature payloadType={detached.get('payloadType')!r}, "
            "expected 'application/vnd.in-toto+json'"
        )
    return problems


def _sign_not_ready_json_problems(stdout: str, returncode: int) -> list[str]:
    """A `sign --json` envelope over a NOT_READY report must refuse -- exit 3, `sign.not_ready` --
    never sign it (item 18.25's own `_readiness_json_problems` "always READY" pattern, applied to
    `sign`'s own readiness gate rather than `readiness` itself)."""
    problems: list[str] = []
    if returncode != 3:
        problems.append(f"sign --json: exit {returncode}, expected 3")
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return problems + [f"sign --json: output is not JSON: {stdout.strip()[:200]!r}"]
    error = envelope.get("error", {})
    key = error.get("key", error.get("message_key"))
    if key != "sign.not_ready":
        problems.append(f"sign --json: error key={key!r}, expected 'sign.not_ready'")
    return problems


def _sign_problems(exe: list[str], runner: Runner, cwd: Path) -> list[str]:
    """`agentce sign <report-dir> --as claimant --profile kms --key <test-key.pem>` against
    `tools/sign_parity_check.py`'s own READY (scenario 1) and NOT_READY (scenario 3) fixtures (item
    18.26's C4), run against the installed artifact the way a user reaches it, network disabled."""
    fixture_dir = cwd / "sign-fixture"
    fixture_dir.mkdir(exist_ok=True)
    keyid, _ = sign_parity_check.known_test_key()
    key_path = sign_parity_check.TEST_KEY

    problems: list[str] = []
    report1 = sign_parity_check.ready_fixture(fixture_dir / "ready")
    proc = runner.run(
        [
            *exe,
            "sign",
            str(report1),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--json",
        ],
        cwd,
        offline=True,
    )
    problems += [
        f"scenario-1: {p}"
        for p in _sign_ready_json_problems(proc.stdout, proc.returncode, keyid)
    ]

    report3 = sign_parity_check.not_ready_fixture(fixture_dir / "not-ready")
    proc = runner.run(
        [
            *exe,
            "sign",
            str(report3),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--json",
        ],
        cwd,
        offline=True,
    )
    problems += [
        f"scenario-3: {p}"
        for p in _sign_not_ready_json_problems(proc.stdout, proc.returncode)
    ]
    return problems


def _verify_json(proc: subprocess.CompletedProcess[str]) -> dict:
    """Parse a `verify` subprocess's stdout, defaulting to `{}` on malformed JSON so callers can
    still report every missing/wrong field as its own problem rather than short-circuiting."""
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {}


_canonical_verify_fixtures_cache: Path | None = None


def _canonical_verify_fixtures() -> Path:
    """`verify_parity_check.build_canonical_fixtures` shells into `engines/python`'s `uv`
    environment to re-derive the same deterministic KMS/keyless keys and DSSE envelopes every
    time; build it once per process and let each of this file's five call sites copy from the
    cached directory instead of paying for its own subprocess spin."""
    global _canonical_verify_fixtures_cache
    if _canonical_verify_fixtures_cache is None:
        cache_dir = Path(tempfile.mkdtemp(prefix="agentce-verify-fixtures-"))
        verify_parity_check.build_canonical_fixtures(cache_dir)
        atexit.register(shutil.rmtree, cache_dir, ignore_errors=True)
        _canonical_verify_fixtures_cache = cache_dir
    return _canonical_verify_fixtures_cache


def _verify_problems(exe: list[str], runner: Runner, cwd: Path) -> list[str]:
    """`agentce verify --catalog/--release` against four of `tools/verify_parity_check.py`'s own
    scenarios (item 18.28's C4), run against the installed artifact the way a user reaches it,
    network disabled: the kms-signed release with its signature byte-flipped (scenario 5 -- the
    item's own named defect, proving the shipped build gives a stable refusal, never
    `internal.unexpected`), the real vendored eu-ai-act catalog with its signature file deleted
    (scenario 3, the exact unsigned sentence), the valid kms-signed release (scenario 4), and the
    valid certificate/keyless-signed release (scenario 6)."""
    canonical = cwd / "verify-fixture"
    shutil.copytree(_canonical_verify_fixtures(), canonical)
    problems: list[str] = []

    tampered = json.loads((canonical / "release-kms.json").read_text(encoding="utf-8"))
    tampered["signatures"][0]["sig"] = verify_parity_check._flip_b64_byte(
        tampered["signatures"][0]["sig"]
    )
    tampered_path = cwd / "release-tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    proc = runner.run(
        [*exe, "verify", "--release", str(tampered_path), "--json"], cwd, offline=True
    )
    envelope = _verify_json(proc)
    if proc.returncode != 3:
        problems.append(
            f"verify --release tampered: exit {proc.returncode}, expected 3"
        )
    if envelope.get("verified") is not False:
        problems.append(
            f"verify --release tampered: verified={envelope.get('verified')!r}, expected false"
        )
    if not envelope.get("reason"):
        problems.append("verify --release tampered: no 'reason' in the output")
    if "error" in envelope:
        problems.append(
            f"verify --release tampered: output carries an 'error' field "
            f"{envelope.get('error')!r} (a crash, not a clean refusal -- the item's own "
            "named defect)"
        )

    unsigned_catalog = cwd / "catalog-unsigned"
    if unsigned_catalog.exists():
        shutil.rmtree(unsigned_catalog)
    shutil.copytree(EU_AI_ACT_DIR, unsigned_catalog)
    (unsigned_catalog / verify_parity_check.CATALOG_SIGNATURE_NAME).unlink()
    proc = runner.run(
        [*exe, "verify", "--catalog", str(unsigned_catalog), "--json"],
        cwd,
        offline=True,
    )
    envelope = _verify_json(proc)
    if proc.returncode != 3 or envelope.get("verified") is not False:
        problems.append(
            f"verify --catalog unsigned: exit {proc.returncode}, "
            f"verified={envelope.get('verified')!r}, expected exit 3, verified=false"
        )
    if envelope.get("reason") != verify_parity_check.UNSIGNED_SENTENCE:
        problems.append(
            f"verify --catalog unsigned: reason={envelope.get('reason')!r}, expected "
            f"{verify_parity_check.UNSIGNED_SENTENCE!r}"
        )

    for label, fixture_name, expect_keyless in (
        ("kms", "release-kms.json", False),
        ("cert", "release-cert.json", True),
    ):
        release_path = cwd / f"release-{label}.json"
        shutil.copyfile(canonical / fixture_name, release_path)
        proc = runner.run(
            [*exe, "verify", "--release", str(release_path), "--json"],
            cwd,
            offline=True,
        )
        envelope = _verify_json(proc)
        if proc.returncode != 0 or envelope.get("verified") is not True:
            problems.append(
                f"verify --release {label}: exit {proc.returncode}, "
                f"verified={envelope.get('verified')!r}, expected exit 0, verified=true"
            )
        if envelope.get("keyless") is not expect_keyless:
            problems.append(
                f"verify --release {label}: keyless={envelope.get('keyless')!r}, "
                f"expected {expect_keyless!r}"
            )

    return problems


def _report_validate_problems(
    exe: list[str],
    runner: Runner,
    cwd: Path,
    qs_out: Path,
    *,
    error_key: str = "message_key",
) -> list[str]:
    """`agentce report --validate <dir>` against this installed artifact's own freshly-written
    quickstart output (item 18.27), run the way a user reaches it (C4), network disabled: a clean
    run validates with no problems; a copy with a real-schema-only OSCAL violation (the same
    discriminating case `report_validate_parity_check.py`'s `case6-oscal-nist` uses) is caught only
    if the vendored NIST 1.1.2 schema actually shipped inside this installed artifact, not merely in
    the repo's build tree -- a packaging gap C1-C3 (which run from the checkout) cannot see; a
    missing `report_dir` refuses before opening any artifact. `error_key` is the error envelope's
    own field name for the message key -- `message_key` for the TS/Java callers this exists for,
    `key` for the self-test's own direct Python-engine call (the same per-engine split
    `report_validate_parity_check.py`'s case16 already documents)."""
    problems: list[str] = []
    proc = runner.run(
        [*exe, "report", "--validate", str(qs_out), "--json"], cwd, offline=True
    )
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        return [
            f"report --validate clean: output is not JSON: {proc.stdout.strip()[:200]!r}"
        ]
    if proc.returncode != 0 or envelope.get("valid") is not True:
        problems.append(
            f"report --validate clean: exit {proc.returncode}, valid={envelope.get('valid')!r}, "
            f"expected exit 0, valid=true (problems={envelope.get('problems')!r})"
        )

    corrupted = cwd / "report-validate-corrupted"
    if corrupted.exists():
        shutil.rmtree(corrupted)
    shutil.copytree(qs_out, corrupted)
    oscal_path = corrupted / "oscal-ar.json"
    oscal = json.loads(oscal_path.read_text(encoding="utf-8"))
    oscal["assessment-results"]["uuid"] = "not-a-uuid"
    oscal_path.write_text(json.dumps(oscal), encoding="utf-8")
    proc = runner.run(
        [*exe, "report", "--validate", str(corrupted), "--json"], cwd, offline=True
    )
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        return problems + [
            f"report --validate oscal-nist: output is not JSON: {proc.stdout.strip()[:200]!r}"
        ]
    marker = "oscal-ar.json (NIST OSCAL 1.1.2)"
    problem_list = envelope.get("problems") or []
    if (
        proc.returncode != 3
        or envelope.get("valid") is not False
        or not any(p.startswith(marker) for p in problem_list)
    ):
        problems.append(
            f"report --validate oscal-nist: exit {proc.returncode}, valid={envelope.get('valid')!r}, "
            f"problems={problem_list!r}, expected exit 3 naming {marker!r} "
            "(the vendored NIST schema may not be packaged into this installed artifact)"
        )

    missing = cwd / "report-validate-does-not-exist"
    proc = runner.run(
        [*exe, "report", "--validate", str(missing), "--json"], cwd, offline=True
    )
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        return problems + [
            f"report --validate missing-dir: output is not JSON: {proc.stdout.strip()[:200]!r}"
        ]
    key = envelope.get("error", {}).get(error_key)
    if proc.returncode != 3 or key != "input.validate_not_a_directory":
        problems.append(
            f"report --validate missing-dir: exit {proc.returncode}, {error_key}={key!r}, "
            "expected exit 3, 'input.validate_not_a_directory'"
        )
    return problems


_ZERO_DIGEST = "sha256:" + "0" * 64


def _manifest_digest(out_dir: Path, catalog_id: str) -> str | None:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return next(
        (
            c.get("digest")
            for c in manifest.get("inputs", {}).get("catalogs", [])
            if c.get("id") == catalog_id
        ),
        None,
    )


def _digest_problem(
    label: str, out_dir: Path, catalog_id: str, expected_digest: str
) -> str | None:
    """`manifest.json`'s digest for `catalog_id` must be the real, live-recomputed content digest --
    never the zero digest, and never a value that merely happens to differ from zero but isn't the
    one an independent recomputation of the same bundled directory produces."""
    got = _manifest_digest(out_dir, catalog_id)
    if got is None:
        return f"{label}: manifest.json has no digest for catalog {catalog_id!r}"
    if got == _ZERO_DIGEST:
        return f"{label}: manifest digest for {catalog_id!r} is the zero digest"
    if got != expected_digest:
        return f"{label}: manifest digest {got!r} != independently recomputed {expected_digest!r}"
    return None


def _section_span_html(text: str, aria_label: str) -> str | None:
    match = re.search(
        rf'<section aria-labelledby="{aria_label}">.*?</section>', text, re.DOTALL
    )
    return match.group(0) if match else None


def _verdict_span_md(text: str) -> str | None:
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("## Verdict")), None
    )
    end = next(
        (i for i, line in enumerate(lines) if line.startswith("## Outcome summary")),
        None,
    )
    if start is None or end is None or end < start:
        return None
    return "\n".join(lines[start : end + 1])


def _verdict_span_html(text: str) -> str | None:
    return _section_span_html(text, "verdict")


def _tally_span_md(text: str) -> str | None:
    """`## Outcome summary` through (not including) the next `## ` heading -- the six-outcome
    tally list itself, not only the section's presence."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("## Outcome summary")),
        None,
    )
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _tally_span_html(text: str) -> str | None:
    return _section_span_html(text, "summary")


def _section_problems(
    label: str,
    fmt: str,
    text: str,
    reference_span: str,
    span_md: Callable[[str], str | None],
    span_html: Callable[[str], str | None],
    missing_name: str,
    differs_name: str,
) -> list[str]:
    span = span_md(text) if fmt == "md" else span_html(text)
    if span is None:
        return [f"{label}: {fmt} report has no {missing_name} section"]
    if span != reference_span:
        return [
            f"{label}: {fmt} {differs_name} differs from the Python reference rendering"
        ]
    return []


def _verdict_problems(
    label: str, fmt: str, text: str, reference_span: str
) -> list[str]:
    """The rendered Verdict section (`## Verdict` through `## Outcome summary` in md; the
    `<section aria-labelledby="verdict">` element in html) must be byte-equal to the Python engine's
    rendering of the identical bundle -- not merely present (C1(h)/C4)."""
    return _section_problems(
        label,
        fmt,
        text,
        reference_span,
        _verdict_span_md,
        _verdict_span_html,
        "Verdict",
        "Verdict section",
    )


def _tally_problems(label: str, fmt: str, text: str, reference_span: str) -> list[str]:
    """The rendered outcome-tally counts (`## Outcome summary`'s bullet list in md; the
    `<section aria-labelledby="summary">` element in html) must be byte-equal to the Python
    engine's rendering of the identical bundle, compared pairwise across engines via the shared
    Python reference (C4's "summary tally ... compared pairwise" clause) -- the Verdict-span check
    above stops at the `## Outcome summary` heading itself and never reaches the counts."""
    return _section_problems(
        label,
        fmt,
        text,
        reference_span,
        _tally_span_md,
        _tally_span_html,
        "Outcome summary",
        "outcome tally",
    )


def _quickstart_parity_problems(
    label: str,
    exe: list[str],
    runner: Runner,
    empty: Path,
    qs_out: Path,
    reference: Path,
    bundled_eu_ai_act: Path | None,
    bundled_quickstart: Path | None,
) -> list[str]:
    """The digest, Verdict-section, and tamper-parity assertions shared by the npm and jar installed-
    artifact checks (C4) -- identical once each caller has located its own bundled `eu-ai-act` catalog
    and `quickstart` corpus directory (a filesystem path for npm, an extracted zip subtree for the
    jar; `None` when the package lacks it, which each caller checks separately)."""
    problems: list[str] = []
    untampered_digest = _manifest_digest(qs_out, "eu-ai-act")
    if bundled_eu_ai_act is not None:
        expected_digest = catalog_digest_check.digest_tree(bundled_eu_ai_act)
        problem = _digest_problem(
            f"{label} quickstart", qs_out, "eu-ai-act", expected_digest
        )
        if problem:
            problems.append(problem)
    for fmt, name in (("md", "report.md"), ("html", "report.html")):
        rendered = qs_out / name
        ref_rendered = reference / name
        if rendered.is_file() and ref_rendered.is_file():
            text = rendered.read_text(encoding="utf-8")
            ref_text = ref_rendered.read_text(encoding="utf-8")
            ref_verdict_span = (
                _verdict_span_md(ref_text)
                if fmt == "md"
                else _verdict_span_html(ref_text)
            )
            ref_tally_span = (
                _tally_span_md(ref_text) if fmt == "md" else _tally_span_html(ref_text)
            )
            problems += [
                f"{label} quickstart: {p}"
                for p in _verdict_problems(label, fmt, text, ref_verdict_span or "")
            ]
            problems += [
                f"{label} quickstart: {p}"
                for p in _tally_problems(label, fmt, text, ref_tally_span or "")
            ]
    if bundled_quickstart is not None:
        problems += _tamper_problems(
            label, exe, runner, empty, bundled_quickstart, untampered_digest
        )
    return problems


def _tamper_catalog(tmp: Path) -> Path:
    """A copy of the real, bundled eu-ai-act catalog with one control file's content changed, so its
    content digest legitimately differs from the original -- while its `catalog.yaml` (excluded from
    the digest, and never copied forward with a corrected value) still carries the now-stale
    `provenance.digest` of the untampered catalog."""
    tampered = tmp / "tampered-eu-ai-act"
    shutil.copytree(EU_AI_ACT_DIR, tampered)
    controls = sorted((tampered / "controls").glob("*.yaml"))
    if not controls:
        raise SystemExit("no control files to tamper with in the eu-ai-act catalog")
    with controls[0].open("a", encoding="utf-8") as fh:
        fh.write("# tampered by installed_artifacts_check\n")
    return tampered


def _tamper_problems(
    label: str,
    exe: list[str],
    runner: Runner,
    tmp: Path,
    quickstart: Path,
    untampered_digest: str | None,
) -> list[str]:
    """`--catalog-dir` pointed at a modified catalog is refused as `input.catalog_unverified` (its
    signature no longer covers the content); with `--allow-unverified-catalog` it produces a
    manifest digest that is a live recomputation of the tampered content -- never the untampered
    run's digest, and never the tampered catalog's own (now-stale) `catalog.yaml` value, which this
    never touches."""
    tampered = _tamper_catalog(tmp)
    out = tmp / f"{label}-tampered-out"
    cmd = [
        *exe,
        "assess",
        "--bundle",
        str(quickstart / "evidence"),
        "--profile",
        str(quickstart / "applicability.yaml"),
        "--domain",
        str(quickstart / "domain.linkml.yaml"),
        "--catalog-dir",
        str(tampered),
        "--out",
        str(out),
    ]
    proc = runner.run(cmd, tmp, offline=True)
    if (
        proc.returncode != 3
        or "input.catalog_unverified" not in proc.stdout + proc.stderr
    ):
        return [
            _fail(
                f"TEETH: {label}: a tampered --catalog-dir was not refused as "
                "input.catalog_unverified",
                proc,
            )
        ]
    proc = runner.run([*cmd, "--allow-unverified-catalog"], tmp, offline=True)
    if proc.returncode not in (0, 1):
        return [_fail(f"{label}: assess against a tampered --catalog-dir", proc)]
    expected = catalog_digest_check.digest_tree(tampered)
    problems = []
    problem = _digest_problem(f"{label} tampered", out, "eu-ai-act", expected)
    if problem:
        problems.append(problem)
    got = _manifest_digest(out, "eu-ai-act")
    if got is not None and got == untampered_digest:
        problems.append(
            f"{label}: the tampered run's digest equals the untampered run's -- "
            "not a live recomputation"
        )
    return problems


def _numerics_problems(engine_cmd: list[str], cwd: Path, runner: Runner) -> list[str]:
    problems: list[str] = []
    files = sorted(VECTORS.glob("*.json"))
    if not files:
        return ["no numerics vector files found"]
    for path in files:
        spec = json.loads(path.read_text(encoding="utf-8"))
        proc = runner.run([*engine_cmd, "numerics", str(path)], cwd, offline=True)
        if proc.returncode != 0:
            problems.append(_fail(f"numerics {path.name}", proc))
            continue
        try:
            got = json.loads(proc.stdout)
        except ValueError:
            problems.append(f"numerics {path.name}: output is not JSON")
            continue
        for case in spec["cases"]:
            if spec["algorithm"] == "clopper_pearson":
                expected: object = {"lower": case["lower"], "upper": case["upper"]}
            else:
                expected = case["expected"]
            if got.get(case["name"]) != expected:
                problems.append(
                    f"numerics {path.name}: {case['name']} = {got.get(case['name'])!r}"
                )
    return problems


def _otel_genai_fixture_problems(
    exe: list[str], runner: Runner, empty: Path
) -> list[str]:
    """Run the shipped package's own ``otel-genai-fixture`` seam verb against the vendored
    ``otel-genai-chat`` fixture (item 18.29 C4): proves the otel-genai adapter port is actually
    present and working inside the installed artifact, not only inside the checkout's own build."""
    fixture_dir = empty / "otel-genai-fixture"
    fixture_dir.mkdir()
    chat = _OTEL_FIXTURES / "otel-genai-chat"
    shutil.copy2(chat / "input.json", fixture_dir / "input.json")
    shutil.copy2(chat / "adapt.json", fixture_dir / "adapt.json")
    proc = runner.run(
        [*exe, "otel-genai-fixture", str(fixture_dir)], empty, offline=True
    )
    if proc.returncode != 0:
        return [_fail("otel-genai-fixture", proc)]
    got_events = [
        json.loads(line.removeprefix("EVENT "))
        for line in proc.stdout.splitlines()
        if line.startswith("EVENT ")
    ]
    want_events = [
        json.loads(line)
        for line in (chat / "expected.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    if got_events != want_events:
        return ["otel-genai-fixture: events do not match expected.jsonl"]
    return []


def check_npm(runner: Runner, *, offline_install: bool) -> list[str]:
    if shutil.which("npm") is None or shutil.which("pnpm") is None:
        return ["npm and pnpm are required to build and install the package"]
    with _scratch("agentce-installed-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        proc = runner.run(["pnpm", "build"], TS_ENGINE, offline=False)
        if proc.returncode != 0:
            return [_fail("build the TypeScript engine", proc)]
        (tmp / "dist").mkdir()
        proc = runner.run(
            ["npm", "pack", "--silent", "--pack-destination", str(tmp / "dist")],
            TS_ENGINE,
            offline=False,
        )
        tarballs = sorted((tmp / "dist").glob("*.tgz"))
        if proc.returncode != 0 or len(tarballs) != 1:
            return [_fail("pack the npm tarball", proc)]
        return check_npm_tarball(runner, tarballs[0], offline_install=offline_install)


def check_npm_tarball(
    runner: Runner, tarball: Path, *, offline_install: bool
) -> list[str]:
    """Install a packed or downloaded tarball into an empty project and run it."""
    version = package_version()
    with _scratch("agentce-installed-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        reference = _python_reference_quickstart(runner, tmp)
        if isinstance(reference, str):
            return [reference]
        spec_version = _python_spec_version(reference)
        if spec_version is None:
            return ["could not determine the python engine's own spec_version"]
        empty = tmp / "empty"
        empty.mkdir()
        (empty / "package.json").write_text(
            '{"name":"consumer","private":true}\n', encoding="utf-8"
        )
        flags = ["--offline"] if offline_install else []
        proc = runner.run(
            [
                "npm",
                "install",
                "--no-audit",
                "--no-fund",
                "--loglevel=error",
                *flags,
                str(tarball),
            ],
            empty,
            offline=False,
        )
        if proc.returncode != 0:
            return [_fail("install the tarball into an empty project", proc)]
        return _npm_run_problems(
            runner, empty, version, reference=reference, spec_version=spec_version
        )


def _npm_run_problems(
    runner: Runner, empty: Path, version: str, *, reference: Path, spec_version: str
) -> list[str]:
    exe = [str(empty / "node_modules" / ".bin" / "agentce")]
    problems: list[str] = []
    proc = runner.run([*exe, "--version"], empty, offline=True)
    if proc.returncode != 0 or proc.stdout.strip() != f"agentce {version}":
        problems.append(
            f"--version printed {proc.stdout.strip()!r}, expected 'agentce {version}'"
        )
    proc = runner.run([*exe, "conformance", "run", "--json"], empty, offline=True)
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        envelope = {}
    if not envelope.get("errors") and not envelope.get("error"):
        problems.append(
            _fail("conformance run --json did not return a stable error envelope", proc)
        )
    # The test seams ship in the package but are not `agentce` commands (18.108).
    seams = [
        "node",
        str(
            empty / "node_modules" / "@agent-conformance" / "cli" / "dist" / "seams.js"
        ),
    ]
    problems += _numerics_problems(seams, empty, runner)
    proc = runner.run([*exe, "--version", "-hx"], empty, offline=True)
    if proc.returncode != 3:
        problems.append(_fail("--version -hx was not refused with exit 3", proc))
    problems += [
        f"npm: {p}"
        for p in _version_problems(exe, runner, empty, "agentce-ts", spec_version)
    ]
    problems += [f"npm: {p}" for p in _diff_problems(exe, runner, empty)]
    problems += [f"npm: {p}" for p in _readiness_problems(exe, runner, empty)]
    problems += [f"npm: {p}" for p in _sign_problems(exe, runner, empty)]
    problems += [f"npm: {p}" for p in _verify_problems(exe, runner, empty)]
    problems += [
        f"npm: {p}" for p in _otel_genai_fixture_problems(seams, runner, empty)
    ]
    package = empty / "node_modules" / "@agent-conformance" / "cli"
    for rel in (
        "schema/agentce-evidence.schema.json",
        "data/catalogs/base",
        "data/corpus/quickstart",
    ):
        if not (package / rel).exists():
            problems.append(f"the installed package lacks {rel}")
    qs_out = empty / "quickstart-out"
    proc = runner.run(
        [*exe, "quickstart", "--out", str(qs_out), "--json"], empty, offline=True
    )
    try:
        qs_envelope = json.loads(proc.stdout)
    except ValueError:
        qs_envelope = {}
    problems += [
        f"quickstart: {p}" for p in assess_smoke_check.check(qs_out, qs_envelope)
    ]
    bundled_eu_ai_act = package / "data" / "catalogs" / "base" / "eu-ai-act"
    bundled_quickstart = package / "data" / "corpus" / "quickstart"
    problems += _quickstart_parity_problems(
        "npm",
        exe,
        runner,
        empty,
        qs_out,
        reference,
        bundled_eu_ai_act if bundled_eu_ai_act.is_dir() else None,
        bundled_quickstart if bundled_quickstart.is_dir() else None,
    )
    problems += [
        f"npm: {p}" for p in _report_validate_problems(exe, runner, empty, qs_out)
    ]
    return problems


def check_jar(runner: Runner, *, offline_install: bool) -> list[str]:
    version = package_version()
    if shutil.which("java") is None:
        return ["java is required to run the jar"]
    gradle = [str(JAVA_ENGINE / "gradlew"), "--no-daemon", "--quiet"]
    if offline_install:
        gradle.append("--offline")
    proc = runner.run([*gradle, "runnableJar"], JAVA_ENGINE, offline=False)
    if proc.returncode != 0:
        return [_fail("build the runnable jar", proc)]
    jars = sorted((JAVA_ENGINE / "build" / "libs").glob(f"agentce-{version}-all.jar"))
    if len(jars) != 1:
        return [f"expected build/libs/agentce-{version}-all.jar, found {len(jars)}"]
    return check_jar_file(runner, jars[0])


def _extract_zip_subtree(archive: Path, prefix: str, dest: Path) -> Path:
    """Every entry of `archive` whose name starts with `prefix`, written under `dest` with the
    prefix stripped -- used to read a jar's bundled catalog/corpus data as real files on disk so
    `agentce assess --catalog-dir`/`--bundle`/`--domain` (which take filesystem paths) can use them."""
    with zipfile.ZipFile(archive) as zf:
        names = [
            n for n in zf.namelist() if n.startswith(prefix) and not n.endswith("/")
        ]
        for name in names:
            target = dest / name[len(prefix) :]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
    return dest


def check_jar_file(runner: Runner, built: Path) -> list[str]:
    """Run a built or downloaded jar from an empty directory."""
    version = package_version()
    with _scratch("agentce-installed-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        reference = _python_reference_quickstart(runner, tmp)
        if isinstance(reference, str):
            return [reference]
        spec_version = _python_spec_version(reference)
        if spec_version is None:
            return ["could not determine the python engine's own spec_version"]
        jar = tmp / built.name
        shutil.copy2(built, jar)
        empty = tmp / "empty"
        empty.mkdir()
        exe = ["java", "-jar", str(jar)]
        problems: list[str] = []
        proc = runner.run([*exe, "--version"], empty, offline=True)
        if proc.returncode != 0 or proc.stdout.strip() != f"agentce {version}":
            problems.append(
                f"--version printed {proc.stdout.strip()!r}, expected 'agentce {version}'"
            )
        proc = runner.run([*exe, "conformance", "run", "--json"], empty, offline=True)
        try:
            envelope = json.loads(proc.stdout)
        except ValueError:
            envelope = {}
        if not envelope.get("errors") and not envelope.get("error"):
            problems.append(
                _fail(
                    "conformance run --json did not return a stable error envelope",
                    proc,
                )
            )
        problems += [
            f"jar: {p}"
            for p in _version_problems(exe, runner, empty, "agentce-java", spec_version)
        ]
        problems += [f"jar: {p}" for p in _diff_problems(exe, runner, empty)]
        problems += [f"jar: {p}" for p in _readiness_problems(exe, runner, empty)]
        problems += [f"jar: {p}" for p in _sign_problems(exe, runner, empty)]
        problems += [f"jar: {p}" for p in _verify_problems(exe, runner, empty)]
        problems += [
            f"jar: {p}"
            for p in _otel_genai_fixture_problems(
                ["java", "-cp", str(jar), "org.agentce.Seams"], runner, empty
            )
        ]
        with zipfile.ZipFile(jar) as zf:
            names = set(zf.namelist())
        for entry in (
            "agentce-evidence.schema.json",
            "catalogs/base/eu-ai-act/catalog.yaml",
            "corpus/quickstart/applicability.yaml",
        ):
            if entry not in names:
                problems.append(f"the jar lacks {entry}")
        qs_out = tmp / "quickstart-out"
        proc = runner.run(
            [*exe, "quickstart", "--out", str(qs_out), "--json"], empty, offline=True
        )
        try:
            qs_envelope = json.loads(proc.stdout)
        except ValueError:
            qs_envelope = {}
        problems += [
            f"quickstart: {p}" for p in assess_smoke_check.check(qs_out, qs_envelope)
        ]
        bundled_eu_ai_act_prefix = "catalogs/base/eu-ai-act/"
        bundled_eu_ai_act = (
            _extract_zip_subtree(jar, bundled_eu_ai_act_prefix, tmp / "jar-eu-ai-act")
            if any(n.startswith(bundled_eu_ai_act_prefix) for n in names)
            else None
        )
        bundled_quickstart_prefix = "corpus/quickstart/"
        bundled_quickstart = (
            _extract_zip_subtree(jar, bundled_quickstart_prefix, tmp / "jar-quickstart")
            if any(n.startswith(bundled_quickstart_prefix) for n in names)
            else None
        )
        problems += _quickstart_parity_problems(
            "jar",
            exe,
            runner,
            empty,
            qs_out,
            reference,
            bundled_eu_ai_act,
            bundled_quickstart,
        )
        problems += [
            f"jar: {p}" for p in _report_validate_problems(exe, runner, empty, qs_out)
        ]
        return problems


CHECKS = {
    "python": check_python,
    "records": check_records,
    "project": check_project,
    "rerun": check_rerun,
    "own_rules": check_own_rules,
    "npm": check_npm,
    "jar": check_jar,
}
# `all` covers the package kinds; `records`, `project`, `rerun` and `own_rules` are their own build
# gates' own checks.
ALL_KINDS = tuple(
    k for k in CHECKS if k not in ("records", "project", "rerun", "own_rules")
)


def run_all(
    kinds: list[str], *, offline: bool, require_netns: bool
) -> dict[str, list[str]]:
    runner = Runner(require_netns)
    return {kind: CHECKS[kind](runner, offline_install=offline) for kind in kinds}


# --- self-test: the check must fail on an inert wheel and on an inert npm package ---------


def _inert_wheel(directory: Path, version: str) -> Path:
    """A wheel that installs an ``agentce`` command which exits 0 having evaluated nothing."""
    name = f"agent_conformance-{version}"
    wheel = directory / f"{name}-py3-none-any.whl"
    dist_info = f"{name}.dist-info"
    files = {
        "agentce/__init__.py": "",
        "agentce/cli.py": "def main():\n    return 0\n",
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: agent-conformance\nVersion: {version}\n",
        f"{dist_info}/WHEEL": "Wheel-Version: 1.0\nGenerator: inert\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist_info}/entry_points.txt": "[console_scripts]\nagentce = agentce.cli:main\n",
    }
    with zipfile.ZipFile(wheel, "w") as zf:
        for path, body in files.items():
            zf.writestr(path, body)
        zf.writestr(
            f"{dist_info}/RECORD",
            "".join(f"{p},,\n" for p in files) + f"{dist_info}/RECORD,,\n",
        )
    return wheel


def _inert_jar(directory: Path) -> Path | None:
    """A runnable jar whose ``agentce`` prints the wrong version and evaluates nothing, else.

    Compiled with the host's own ``javac``/``jar`` (the same JDK the real jar check needs), so this
    returns ``None`` where neither is on PATH rather than fabricate bytecode by hand.
    """
    if shutil.which("javac") is None or shutil.which("jar") is None:
        return None
    src = directory / "Inert.java"
    src.write_text(
        "public class Inert {\n"
        "    public static void main(String[] args) {\n"
        '        if (args.length > 0 && args[0].equals("--version")) {\n'
        '            System.out.println("agentce 0.0.0");\n'
        "        }\n"
        "        // every other invocation, including quickstart, exits 0 having evaluated nothing\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    classes = directory / "inert-classes"
    classes.mkdir()
    proc = subprocess.run(
        ["javac", "-d", str(classes), str(src)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"self-test: could not compile the inert jar fixture: {proc.stderr}"
        )
    jar = directory / "inert.jar"
    proc = subprocess.run(
        [
            "jar",
            "--create",
            "--file",
            str(jar),
            "--main-class",
            "Inert",
            "-C",
            str(classes),
            ".",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"self-test: could not package the inert jar fixture: {proc.stderr}"
        )
    return jar


def self_test() -> int:
    runner = Runner(require_netns=False)
    failures: list[str] = []
    with _scratch("agentce-installed-selftest-") as raw:
        tmp = Path(raw)
        py_reference = _python_reference_quickstart(runner, tmp)
        if isinstance(py_reference, str):
            failures.append(
                f"could not build the python reference quickstart: {py_reference}"
            )
            py_reference = (
                tmp / "py-reference"
            )  # missing; guarded reads below just skip
        py_spec_version = _python_spec_version(py_reference) or "0.0"
        if py_reference.is_dir():
            # Exercises `_report_validate_problems`'s three real branches (clean, oscal-nist,
            # missing-dir) against the real Python engine binary and its own quickstart output --
            # not just the inert npm/jar fixtures below, which only ever reach the clean branch's
            # earliest JSON-parse failure and never prove the oscal-nist/missing-dir logic sound.
            real_validate_problems = _report_validate_problems(
                ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "agentce"],
                runner,
                tmp,
                py_reference,
                error_key="key",
            )
            if real_validate_problems:
                failures.append(
                    "report --validate against a real python quickstart found problems: "
                    + "; ".join(real_validate_problems)
                )
        good = tmp / "good"
        (good / "packs" / "p").mkdir(parents=True)
        for name in CANONICAL_OUTPUTS:
            (good / name).write_text(f"{name}\n", encoding="utf-8")
        (good / "packs" / "p" / "pack.json").write_text("{}\n", encoding="utf-8")
        if compare_outputs(good, good):
            failures.append("identical outputs were reported as different")
        bad = tmp / "bad"
        shutil.copytree(good, bad)
        (bad / "report.md").write_text("", encoding="utf-8")
        (bad / "assertions.json").write_text("changed\n", encoding="utf-8")
        (bad / "packs" / "p" / "pack.json").unlink()
        found = compare_outputs(good, bad)
        for expected in (
            "no report.md",
            "assertions.json differs",
            "evidence packs differ",
        ):
            if not any(expected in p for p in found):
                failures.append(
                    f"a broken output set was not reported: missing '{expected}'"
                )
        leak = tmp / "leak"
        shutil.copytree(good, leak)
        (leak / "report.md").write_text(f"see {ROOT}/corpus\n", encoding="utf-8")
        if not any("checkout path" in p for p in compare_outputs(good, leak)):
            failures.append("a report embedding the checkout path was accepted")

        if _budget_problem("t", 10, 300) is not None:
            failures.append("a fast first report was reported as over budget")
        if _budget_problem("t", 400, 300) is None:
            failures.append("a first report over the budget was accepted")
        if _budget_problem("t", 400, 300, what="install and re-run") != (
            "t: install and re-run took 400s, over the 300s budget"
        ):
            failures.append("a custom budget label (VG-RERUN-TIME's own) was not used")

        # An inert wheel installs and exits 0 but evaluates nothing: it must be rejected.
        inert = _inert_wheel(tmp, package_version())
        reference = tmp / "reference"
        shutil.copytree(good, reference)
        problems = check_python_artifact(
            runner, inert, tmp, reference, offline_install=False, label="inert"
        )
        if not problems:
            failures.append(
                "an inert wheel that evaluates nothing passed the installed-artifact check"
            )
        installed = _install(runner, inert, tmp, "inert-records", offline_install=False)
        if isinstance(installed, str):
            failures.append(f"could not install the inert wheel: {installed}")
        elif not _records_problems(
            runner, installed[0], installed[1], "inert", time.monotonic()
        ):
            failures.append(
                "an inert wheel that reads no records passed the records check"
            )
        if _outside_checkout(ROOT):
            failures.append("the checkout was reported to be outside itself")

        # An installed npm package whose command prints the wrong version, answers no envelope, solves
        # no vector, and ships no data must be rejected on every count.
        consumer = tmp / "consumer"
        (consumer / "node_modules" / ".bin").mkdir(parents=True)
        (consumer / "node_modules" / "@agent-conformance" / "cli").mkdir(parents=True)
        inert_bin = consumer / "node_modules" / ".bin" / "agentce"
        inert_bin.write_text("#!/bin/sh\necho agentce 0.0.0\n", encoding="utf-8")
        inert_bin.chmod(0o755)
        found = _npm_run_problems(
            runner,
            consumer,
            package_version(),
            reference=py_reference,
            spec_version=py_spec_version,
        )
        for expected in (
            "--version printed",
            "stable error envelope",
            "numerics",
            "lacks data/catalogs/base",
            "quickstart: ",
            "version: ",
            "report --validate",
            "otel-genai-fixture",
        ):
            if not any(expected in p for p in found):
                failures.append(
                    f"an inert npm install was accepted: missing '{expected}'"
                )

        # A jar whose agentce prints the wrong version, answers no envelope, evaluates nothing on
        # quickstart, and carries no vendored data must be rejected on every count too.
        inert_jar = _inert_jar(tmp)
        if inert_jar is None:
            failures.append(
                "self-test: javac/jar are not on PATH, cannot prove the jar check has teeth"
            )
        else:
            found = check_jar_file(runner, inert_jar)
            for expected in (
                "--version printed",
                "stable error envelope",
                "the jar lacks",
                "quickstart: ",
                "version: ",
                "report --validate",
                "otel-genai-fixture",
            ):
                if not any(expected in p for p in found):
                    failures.append(
                        f"an inert jar install was accepted: missing '{expected}'"
                    )

        # --- item 18.22: version --json, the real catalog digest, and the Verdict section must each
        # discriminate a broken installed artifact -- proven directly against the four new seeded-fault
        # fixtures the contract names, each paired with a positive case so a correct fixture is never
        # rejected either. ------------------------------------------------------------------------

        # 1. `version --json` must reject a plain-text response where JSON was expected.
        if not _version_json_problems(
            "agentce-ts 0.1.0 (spec 0.6)\nno_ml: pass\n", 0, "agentce-ts", "0.6"
        ):
            failures.append("a plain-text version --json response was accepted")
        good_envelope = json.dumps(
            {
                "engine": "agentce-ts",
                "spec_version": "0.6",
                "no_ml": "pass",
                "no_ml_detail": {"result": "pass", "denylisted_present": []},
            }
        )
        if _version_json_problems(good_envelope, 0, "agentce-ts", "0.6"):
            failures.append("a correct version --json envelope was rejected")

        # 2. a manifest digest of all zeros must be rejected as the zero digest specifically, not
        #    merely because it differs from the expected value.
        zero_manifest_dir = tmp / "zero-manifest"
        zero_manifest_dir.mkdir()
        (zero_manifest_dir / "manifest.json").write_text(
            json.dumps(
                {"inputs": {"catalogs": [{"id": "eu-ai-act", "digest": _ZERO_DIGEST}]}}
            ),
            encoding="utf-8",
        )
        zero_problem = _digest_problem(
            "test", zero_manifest_dir, "eu-ai-act", "sha256:" + "1" * 64
        )
        if zero_problem is None or "zero digest" not in zero_problem:
            failures.append(
                "a zero-digest manifest was not rejected as the zero digest specifically"
            )

        # 3. a manifest digest equal to a stale (never recomputed) value -- as a tampered catalog's own
        #    catalog.yaml would still carry -- must be rejected, and the correct, live-recomputed
        #    digest must be accepted.
        real_eu_ai_act_digest = catalog_digest_check.digest_tree(EU_AI_ACT_DIR)
        stale_manifest_dir = tmp / "stale-manifest"
        stale_manifest_dir.mkdir()
        (stale_manifest_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "inputs": {
                        "catalogs": [
                            {"id": "eu-ai-act", "digest": "sha256:" + "f" * 64}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        if (
            _digest_problem(
                "test", stale_manifest_dir, "eu-ai-act", real_eu_ai_act_digest
            )
            is None
        ):
            failures.append("a stale, unrecomputed manifest digest was accepted")
        good_manifest_dir = tmp / "good-manifest"
        good_manifest_dir.mkdir()
        (good_manifest_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "inputs": {
                        "catalogs": [
                            {"id": "eu-ai-act", "digest": real_eu_ai_act_digest}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        if (
            _digest_problem(
                "test", good_manifest_dir, "eu-ai-act", real_eu_ai_act_digest
            )
            is not None
        ):
            failures.append("a correct, live-recomputed manifest digest was rejected")

        # 4. a report missing its Verdict section entirely must be rejected, in both formats; a
        #    byte-identical section (with unrelated trailing content) must be accepted.
        if not _verdict_problems(
            "test",
            "md",
            "# report\n\n## Outcome summary\n",
            "## Verdict\n\nx\n\n## Outcome summary",
        ):
            failures.append("a markdown report with no Verdict section was accepted")
        if not _verdict_problems(
            "test",
            "html",
            '<section aria-labelledby="summary">...</section>',
            '<section aria-labelledby="verdict">x</section>',
        ):
            failures.append("an html report with no Verdict section was accepted")
        matching_span = "## Verdict\n\nx\n\n## Outcome summary"
        if _verdict_problems("test", "md", matching_span + "\nmore\n", matching_span):
            failures.append("a byte-identical markdown Verdict section was rejected")

        # 5. the outcome-tally counts (C4's "compared pairwise" clause) must be caught when they
        #    differ from the reference, even though the Verdict-span check above stops at the
        #    heading and never reaches them; a byte-identical tally with unrelated surrounding
        #    content (a differently-formatted assertions section) must still be accepted. The
        #    reference span is derived through the same extractor as the candidates so the "up to
        #    the next heading" boundary's trailing blank line can't cause a spurious mismatch.
        md_reference_text = (
            "## Outcome summary\n\n- `conformant`: 3\n- `non-conformant`: 0"
            "\n\n## Assertions\n\nsomething\n"
        )
        md_tally_reference = _tally_span_md(md_reference_text) or ""
        if not _tally_problems(
            "test",
            "md",
            md_reference_text.replace("conformant`: 3", "conformant`: 1"),
            md_tally_reference,
        ):
            failures.append(
                "a markdown report with a differing outcome tally was accepted"
            )
        if _tally_problems(
            "test",
            "md",
            md_reference_text.replace("something", "an unrelated assertions section"),
            md_tally_reference,
        ):
            failures.append(
                "a byte-identical markdown outcome tally was rejected over unrelated trailing content"
            )
        html_reference_text = (
            '<section aria-labelledby="summary"><h2 id="summary">Outcome summary</h2>'
            "<ul><li>conformant: 3</li></ul></section>"
            '<section aria-labelledby="assertions">something</section>'
        )
        html_tally_reference = _tally_span_html(html_reference_text) or ""
        if not _tally_problems(
            "test",
            "html",
            html_reference_text.replace("conformant: 3", "conformant: 1"),
            html_tally_reference,
        ):
            failures.append(
                "an html report with a differing outcome tally was accepted"
            )
        if _tally_problems(
            "test",
            "html",
            html_reference_text.replace("something", "an unrelated assertions section"),
            html_tally_reference,
        ):
            failures.append(
                "a byte-identical html outcome tally was rejected over unrelated trailing content"
            )

        # 6. item 18.24's C4: a `diff` implementation that always reports "no differences" (the
        #    contract's own named seeded fault) must be rejected by the `--json` envelope check even
        #    though it might still exit non-zero for an unrelated reason; a correct envelope, a
        #    correct `--format md` rendering, and a correct missing-file error must each be accepted.
        with tempfile.TemporaryDirectory(prefix="diff-selftest-") as raw:
            diff_a, diff_b = diff_parity_check.build_fixture(Path(raw))
            diff_groups = diff_parity_check.what_changed_groups(
                json.loads(diff_a.read_text(encoding="utf-8")),
                json.loads(diff_b.read_text(encoding="utf-8")),
            )
        diff_expected_keys = {
            entry[0] for group in diff_groups.values() for entry in group
        }
        zero_changes_envelope = json.dumps({"diff": [], "changed": 0})
        if not _diff_json_problems(zero_changes_envelope, 1, diff_expected_keys):
            failures.append(
                "a diff implementation that always reports zero changes was accepted"
            )
        good_diff_envelope = json.dumps(
            {
                "diff": [
                    {"control": control, "subject": subject}
                    for control, subject in sorted(diff_expected_keys)
                ],
                "changed": len(diff_expected_keys),
            }
        )
        if _diff_json_problems(good_diff_envelope, 1, diff_expected_keys):
            failures.append("a correct diff --json envelope was rejected")
        if not _diff_md_problems("no differences.\n"):
            failures.append(
                "a diff --format md rendering with no '## What changed' section was accepted"
            )
        if _diff_md_problems("## What changed\n\n### Closed (1)\n- x @ y: a -> b\n"):
            failures.append("a correct diff --format md rendering was rejected")
        if not _diff_missing_file_problems(
            json.dumps({"error": {"message_key": "internal.unexpected"}}), 0
        ):
            failures.append(
                "a diff missing-file response with the wrong exit code and key was accepted"
            )
        if _diff_missing_file_problems(
            json.dumps({"error": {"message_key": "input.report_b_missing"}}), 3
        ):
            failures.append("a correct diff missing-file response was rejected")

        # 7. item 18.25's C4: a `readiness` implementation that always reports READY (the contract's
        #    own named seeded fault) must be rejected on scenario-3's own NOT_READY fixture even
        #    though it might still exit non-zero for an unrelated reason; a correct envelope and a
        #    correct missing-`report_dir` error must each be accepted.
        with tempfile.TemporaryDirectory(prefix="readiness-selftest-") as raw:
            report_dir = Path(raw) / "report"
            report_dir.mkdir()
            always_ready_report = report_dir / "report-readiness-2026-01-01.md"
            always_ready_report.write_text(
                "# Report readiness — READY\n", encoding="utf-8"
            )
            always_ready_envelope = json.dumps(
                {"verdict": "READY", "reasons": [], "report": str(always_ready_report)}
            )
            if not _readiness_json_problems(always_ready_envelope, 0, "NOT READY", 1):
                failures.append(
                    "a readiness implementation that always reports READY was accepted on a "
                    "NOT_READY fixture"
                )
            good_report = report_dir / "report-readiness-2026-01-02.md"
            good_report.write_text("# Report readiness — NOT READY\n", encoding="utf-8")
            good_envelope = json.dumps(
                {
                    "verdict": "NOT READY",
                    "reasons": ["integrity failed on stream gw"],
                    "report": str(good_report),
                }
            )
            if _readiness_json_problems(good_envelope, 1, "NOT READY", 1):
                failures.append("a correct readiness --json envelope was rejected")
        if not _readiness_missing_report_dir_problems(
            json.dumps({"error": {"message_key": "internal.unexpected"}}), 0
        ):
            failures.append(
                "a readiness missing-report_dir response with the wrong exit code and key was accepted"
            )
        if _readiness_missing_report_dir_problems(
            json.dumps({"error": {"message_key": "input.report_dir_missing"}}), 3
        ):
            failures.append(
                "a correct readiness missing-report_dir response was rejected"
            )

        # 8. item 18.26's C4: a `sign` implementation that writes a signature without checking
        #    readiness first (the contract's own named seeded fault) must be rejected on a
        #    NOT_READY report even though it exits "successfully"; a correct refusal and a correct
        #    signed envelope (with a real, readable detached signature file) must each be accepted.
        unchecked_sign = json.dumps(
            {
                "keyid": "sha256:deadbeef",
                "signature": "/nonexistent/claimant-kms.dsse.json",
            }
        )
        if not _sign_not_ready_json_problems(unchecked_sign, 0):
            failures.append(
                "a sign implementation that signs a NOT_READY report without checking "
                "readiness first was accepted"
            )
        good_refusal = json.dumps(
            {
                "error": {
                    "message_key": "sign.not_ready",
                    "detail": "the report is NOT READY: x.",
                }
            }
        )
        if _sign_not_ready_json_problems(good_refusal, 3):
            failures.append("a correct sign NOT_READY refusal was rejected")
        with tempfile.TemporaryDirectory(prefix="sign-selftest-") as raw:
            sig_path = Path(raw) / "claimant-kms.dsse.json"
            sig_path.write_text(
                json.dumps(
                    {
                        "payloadType": "application/vnd.in-toto+json",
                        "payload": "x",
                        "signatures": [{"keyid": "sha256:deadbeef", "sig": "y"}],
                    }
                ),
                encoding="utf-8",
            )
            good_ready = json.dumps(
                {"keyid": "sha256:deadbeef", "signature": str(sig_path)}
            )
            if _sign_ready_json_problems(good_ready, 0, "sha256:deadbeef"):
                failures.append("a correct sign READY envelope was rejected")
            wrong_keyid = json.dumps(
                {"keyid": "sha256:wrong", "signature": str(sig_path)}
            )
            if not _sign_ready_json_problems(wrong_keyid, 0, "sha256:deadbeef"):
                failures.append("a sign envelope with the wrong keyid was accepted")
            missing_sig = json.dumps(
                {
                    "keyid": "sha256:deadbeef",
                    "signature": str(Path(raw) / "missing.json"),
                }
            )
            if not _sign_ready_json_problems(missing_sig, 0, "sha256:deadbeef"):
                failures.append(
                    "a sign envelope naming a signature file that does not exist was accepted"
                )

        # 9. item 18.28's C4: a `verify` implementation that re-raises instead of soft-failing on a
        #    signature that fails to verify (the item's own named defect) must be rejected, even
        #    though it still exits non-zero; the real checkout's own Python engine (which carries
        #    the real fix) must be accepted.
        crashy = tmp / "crashy-cwd"
        crashy.mkdir()
        crashy_exe = crashy / "agentce"
        crashy_exe.write_text(
            "#!/bin/sh\n"
            'echo \'{"error": {"key": "internal.unexpected", "message": "boom"}}\'\n'
            "exit 1\n",
            encoding="utf-8",
        )
        crashy_exe.chmod(0o755)
        if not _verify_problems([str(crashy_exe)], runner, crashy):
            failures.append(
                "a verify implementation that re-raises instead of soft-failing was accepted"
            )
        real_verify_cwd = tmp / "real-verify-cwd"
        real_verify_cwd.mkdir()
        real_verify_problems = _verify_problems(
            ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "agentce"],
            runner,
            real_verify_cwd,
        )
        if real_verify_problems:
            failures.append(
                "the real python engine's own verify command was rejected: "
                + "; ".join(real_verify_problems)
            )
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("installed_artifacts_check self-test: 20 cases discriminate")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="installed_artifacts_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("kind", nargs="?", choices=[*CHECKS, "all"])
    parser.add_argument(
        "--offline",
        action="store_true",
        help="resolve install-time dependencies from the local cache only",
    )
    parser.add_argument(
        "--require-netns",
        action="store_true",
        help="fail unless run steps can be network-isolated",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.kind is None:
        parser.error("choose python, npm, jar, or all")
    kinds = list(ALL_KINDS) if args.kind == "all" else [args.kind]
    results = run_all(kinds, offline=args.offline, require_netns=args.require_netns)
    failed = {k: v for k, v in results.items() if v}
    if args.json:
        print(json.dumps({"ok": not failed, "problems": results}, sort_keys=True))
    else:
        for kind, problems in results.items():
            print(f"{kind}: {'FAIL' if problems else 'ok'}")
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
