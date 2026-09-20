"""The built wheel and sdist carry the engine's data and the wheel works on its own (SPEC §13.4 AX-1).

The tests build the real distributions, put only the wheel's contents on the import path, and run
``quickstart``, ``assess``, and ``readiness`` from an empty directory with no checkout in reach.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

_ENGINE_DIR = Path(__file__).resolve().parents[1]


def _clean_env() -> dict[str, str]:
    """The environment without the active virtualenv or pytest-cov's subprocess hooks, so the child
    process measures nothing of this checkout and sees only what the wheel carries."""
    return {
        k: v
        for k, v in os.environ.items()
        if k != "VIRTUAL_ENV" and not k.startswith("COV_CORE_")
    }


def _build_dists(out: Path) -> tuple[Path, Path]:
    """Build the sdist and the wheel (built from that sdist) and return ``(wheel, sdist)``."""
    # Offline first (the repository's tests run with no network); fall back for a cold cache.
    for offline in (["--offline"], []):
        proc = subprocess.run(
            ["uv", "build", "--sdist", "--wheel", *offline, "--out-dir", str(out)],
            cwd=_ENGINE_DIR,
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        if proc.returncode == 0:
            return next(out.glob("*.whl")), next(out.glob("*.tar.gz"))
    raise AssertionError(f"uv build failed: {proc.stderr[-400:]}")


def test_built_wheel_runs_quickstart_outside_any_checkout(tmp_path: Path) -> None:
    wheel, sdist = _build_dists(tmp_path / "dist")
    with tarfile.open(sdist) as tarball:
        packed = tarball.getnames()
    assert any(
        n.endswith("agentce/data/catalogs/base/eu-ai-act/catalog.yaml") for n in packed
    )
    assert any(
        n.endswith("agentce/data/corpus/quickstart/applicability.yaml") for n in packed
    )
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
        names = archive.namelist()
    assert "agentce/data/catalogs/base/eu-ai-act/catalog.yaml" in names
    assert "agentce/data/corpus/quickstart/applicability.yaml" in names

    workdir = tmp_path / "empty"
    workdir.mkdir()
    env = _clean_env()
    env["PYTHONPATH"] = str(site)  # ahead of the editable install of this checkout
    probe = subprocess.run(
        [sys.executable, "-c", "import agentce; print(agentce.__file__)"],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )
    assert Path(probe.stdout.strip()).is_relative_to(site), probe.stdout + probe.stderr

    out = workdir / "out"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentce.cli",
            "quickstart",
            "--out",
            str(out),
            "--json",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )
    assert run.returncode == 0, run.stderr[-600:]
    envelope = json.loads(run.stdout)
    assert envelope["quickstart"] == "ok"
    assert isinstance(envelope["assertions"], int) and envelope["assertions"] > 0
    assert (out / "assertions.json").is_file()


def test_built_wheel_assess_and_readiness_default_to_the_bundled_catalog(
    tmp_path: Path,
) -> None:
    wheel, _ = _build_dists(tmp_path / "dist")
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    project = site / "agentce" / "data" / "corpus" / "quickstart"
    workdir = tmp_path / "empty"
    workdir.mkdir()
    env = _clean_env()
    env["PYTHONPATH"] = str(site)
    out = workdir / "out"

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agentce.cli", *args, "--json"],
            cwd=workdir,
            capture_output=True,
            text=True,
            env=env,
        )

    # No --catalog and no --catalog-dir: the catalog the profile declares resolves from the wheel.
    assessed = run(
        "assess",
        "--bundle", str(project / "evidence"),
        "--profile", str(project / "applicability.yaml"),
        "--domain", str(project / "domain.linkml.yaml"),
        "--out", str(out),
    )  # fmt: skip
    assert assessed.returncode == 0, assessed.stderr[-600:]
    assert json.loads(assessed.stdout)["assertions"] > 0

    ready = run("readiness", str(out))
    assert ready.returncode in (0, 1), ready.stderr[-600:]
    assert json.loads(ready.stdout)["verdict"]
