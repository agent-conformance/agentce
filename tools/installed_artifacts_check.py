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

Usage:
    installed_artifacts_check.py {python,records,rerun,npm,jar,all} [--offline] [--require-netns] [--json]
    installed_artifacts_check.py --self-test
"""

from __future__ import annotations

import argparse
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
    return problems + _lens_problems(runner, venv, empty, label)


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
            return [_fail("build the wheel", proc)]
        started = time.monotonic()
        installed = _install(
            runner, wheels[0], tmp, "wheel", offline_install=offline_install
        )
        if isinstance(installed, str):
            return [installed]
        venv, empty = installed
        return _records_problems(runner, venv, empty, "wheel", started)


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
            return [_fail("build the wheel", proc)]

        # 1. Sender venv (untimed): package and sign corpus/quickstart for sharing.
        sender_installed = _install(
            runner, wheels[0], tmp, "sender", offline_install=offline_install
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
            runner, wheels[0], tmp, "recipient", offline_install=offline_install
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
        tampered_report = recipient_empty / "tampered-report-out"
        shutil.copytree(sender_out, tampered_report)
        report_md = tampered_report / "report.md"
        report_md.write_bytes(report_md.read_bytes() + b"TAMPER")
        proc = runner.run(
            [
                recipient_agentce,
                "verify",
                "--report",
                str(tampered_report),
                "--signer-trust-root",
                str(trust_root_copy),
                "--json",
            ],
            recipient_empty,
            offline=True,
        )
        if proc.returncode != 3 or "report_output_tampered" not in proc.stdout:
            problems.append(
                _fail(
                    "TEETH: a tampered report.md was not refused as report_output_tampered",
                    proc,
                )
            )

        tampered_evidence = recipient_empty / "tampered-evidence-out"
        shutil.copytree(sender_out, tampered_evidence)
        event_file = next(
            (tampered_evidence / "bundle" / "evidence" / "events").glob("*.jsonl")
        )
        event_file.write_bytes(event_file.read_bytes() + b"TAMPER")
        proc = runner.run(
            [
                recipient_agentce,
                "verify",
                "--report",
                str(tampered_evidence),
                "--signer-trust-root",
                str(trust_root_copy),
                "--json",
            ],
            recipient_empty,
            offline=True,
        )
        if proc.returncode != 3 or "report_evidence_tampered" not in proc.stdout:
            problems.append(
                _fail(
                    "TEETH: a tampered evidence file was not refused as report_evidence_tampered",
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
    """`--catalog-dir` pointed at a modified catalog produces a manifest digest that is a live
    recomputation of the tampered content -- never the untampered run's digest, and never the
    tampered catalog's own (now-stale) `catalog.yaml` value, which this never touches."""
    tampered = _tamper_catalog(tmp)
    out = tmp / f"{label}-tampered-out"
    proc = runner.run(
        [
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
        ],
        tmp,
        offline=True,
    )
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
    problems += _numerics_problems(exe, empty, runner)
    problems += [
        f"npm: {p}"
        for p in _version_problems(exe, runner, empty, "agentce-ts", spec_version)
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
        return problems


CHECKS = {
    "python": check_python,
    "records": check_records,
    "rerun": check_rerun,
    "npm": check_npm,
    "jar": check_jar,
}
# `all` covers the package kinds; `records` and `rerun` are their own build gates' own checks.
ALL_KINDS = tuple(k for k in CHECKS if k not in ("records", "rerun"))


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
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("installed_artifacts_check self-test: 16 cases discriminate")
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
