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

The TypeScript and Java engines do not implement ``quickstart`` yet, so those two checks prove only what
each artifact really offers today; the checks tighten when they do.

Network cut-off: inside a network namespace (``unshare -rn``) where the kernel allows it, else dead
proxies plus the offline switches of each tool. ``--require-netns`` makes the namespace mandatory (CI).
Install steps may fetch third-party dependencies; every run step is offline.

Usage:
    installed_artifacts_check.py {python,npm,jar,all} [--offline] [--require-netns] [--json]
    installed_artifacts_check.py --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
VECTORS = ROOT / "spec" / "rules" / "numerics-vectors" / "cases"
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


def compare_outputs(reference: Path, installed: Path) -> list[str]:
    problems: list[str] = []
    for name in CANONICAL_OUTPUTS:
        ref, got = reference / name, installed / name
        if not got.is_file() or got.stat().st_size == 0:
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
    venv = tmp / f"venv-{label}"
    empty = tmp / f"empty-{label}"
    empty.mkdir()
    out = tmp / f"out-{label}"
    proc = runner.run(["uv", "venv", str(venv)], tmp, offline=False)
    if proc.returncode != 0:
        return [_fail(f"{label}: create venv", proc)]
    flags = ["--offline"] if offline_install else []
    proc = runner.run(
        ["uv", "pip", "install", *flags, str(artifact)],
        tmp,
        offline=False,
        env={"VIRTUAL_ENV": str(venv)},
    )
    if proc.returncode != 0:
        return [_fail(f"{label}: install {artifact.name}", proc)]
    proc = runner.run(
        [str(venv / "bin" / "agentce"), "quickstart", "--out", str(out)],
        empty,
        offline=True,
    )
    if proc.returncode != 0:
        return [_fail(f"{label}: agentce quickstart from an empty directory", proc)]
    return [f"{label}: {p}" for p in compare_outputs(reference, out)]


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
        return _npm_run_problems(runner, empty, version)


def _npm_run_problems(runner: Runner, empty: Path, version: str) -> list[str]:
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
    package = empty / "node_modules" / "@agent-conformance" / "cli"
    for rel in (
        "schema/agentce-evidence.schema.json",
        "data/catalogs/base",
        "data/corpus/quickstart",
    ):
        if not (package / rel).exists():
            problems.append(f"the installed package lacks {rel}")
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


def check_jar_file(runner: Runner, built: Path) -> list[str]:
    """Run a built or downloaded jar from an empty directory."""
    version = package_version()
    with _scratch("agentce-installed-") as raw:
        tmp = Path(raw)
        assert _outside_checkout(tmp)
        jar = tmp / built.name
        shutil.copy2(built, jar)
        empty = tmp / "empty"
        empty.mkdir()
        problems: list[str] = []
        proc = runner.run(["java", "-jar", str(jar), "--version"], empty, offline=True)
        if proc.returncode != 0 or proc.stdout.strip() != f"agentce {version}":
            problems.append(
                f"--version printed {proc.stdout.strip()!r}, expected 'agentce {version}'"
            )
        proc = runner.run(
            ["java", "-jar", str(jar), "conformance", "run", "--json"],
            empty,
            offline=True,
        )
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
        with zipfile.ZipFile(jar) as zf:
            names = set(zf.namelist())
        for entry in (
            "agentce-evidence.schema.json",
            "catalogs/base/eu-ai-act/catalog.yaml",
            "corpus/quickstart/applicability.yaml",
        ):
            if entry not in names:
                problems.append(f"the jar lacks {entry}")
        return problems


CHECKS = {"python": check_python, "npm": check_npm, "jar": check_jar}


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


def self_test() -> int:
    runner = Runner(require_netns=False)
    failures: list[str] = []
    with _scratch("agentce-installed-selftest-") as raw:
        tmp = Path(raw)
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
        found = _npm_run_problems(runner, consumer, package_version())
        for expected in (
            "--version printed",
            "stable error envelope",
            "numerics",
            "lacks data/catalogs/base",
        ):
            if not any(expected in p for p in found):
                failures.append(
                    f"an inert npm install was accepted: missing '{expected}'"
                )
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("installed_artifacts_check self-test: 6 cases discriminate")
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
    kinds = list(CHECKS) if args.kind == "all" else [args.kind]
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
