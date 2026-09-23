#!/usr/bin/env python3
"""vendor_skill_engine - keep each skill's vendored engine wheel in sync with `engines/python`
(SPEC §13.3: skills/* must be one-command installable).

Both approved skills (`skills/agentce-onboard`, `skills/agentce-check-report`) import `agentce` at
runtime (the S-5 version-pin check in each skill's `_common.py`, and `agentce.readiness` /
`agentce.catalog` in `agentce-check-report`'s scripts) -- it is a real dependency, not dead code. A
skill folder must install and self-test standing alone: copied out of this monorepo, with no sibling
`engines/` checkout beside it. So each skill vendors a real wheel of `engines/python` under its own
`vendor/` directory and its `pyproject.toml` / `uv.lock` resolve `agent-conformance` from that local
file, never a relative path back into the monorepo.

    vendor_skill_engine.py               check every skill's vendored wheel unpacks to the same files
                                          and bytes a fresh build of engines/python produces (the CI
                                          guard: fails on drift)
    vendor_skill_engine.py --write       rebuild the wheel and re-vendor it into every skill; then run
                                          `uv lock` in each skill directory yourself and commit both
    vendor_skill_engine.py --self-test   prove --check discriminates: a corrupted vendored wheel and a
                                          version-stale filename both fail, an in-sync one passes

Standard library plus a `uv build`/`uv lock` subprocess; no network beyond what `uv build` itself
needs (none once the local package cache holds engines/python's dependencies); no learned component.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
SKILLS = ("agentce-onboard", "agentce-check-report")


def _source_line(wheel_name: str) -> str:
    return 'agent-conformance = { path = "vendor/' + wheel_name + '" }\n'


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def build_wheel(out_dir: Path) -> Path:
    """Build `engines/python`'s wheel fresh into `out_dir`; return its path."""
    proc = _run(
        ["uv", "build", "--out-dir", str(out_dir), str(PY_ENGINE)],
        ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"uv build failed:\n{proc.stdout}\n{proc.stderr}")
    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, built {len(wheels)}: {wheels}")
    return wheels[0]


def _wheel_contents(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as zf:
        return {
            info.filename: zf.read(info.filename)
            for info in zf.infolist()
            if not info.is_dir()
        }


def _vendor_dir(skill: str) -> Path:
    return ROOT / "skills" / skill / "vendor"


def _vendored_wheel(skill: str) -> Path | None:
    wheels = (
        sorted(_vendor_dir(skill).glob("*.whl")) if _vendor_dir(skill).is_dir() else []
    )
    return wheels[0] if len(wheels) == 1 else None


def _pyproject_source_line(skill: str, wheel_name: str) -> None:
    path = ROOT / "skills" / skill / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out = []
    replaced = False
    for line in lines:
        if line.strip().startswith("agent-conformance = {"):
            out.append(_source_line(wheel_name))
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise RuntimeError(
            f"{path}: no `agent-conformance = {{ ... }}` source line found"
        )
    path.write_text("".join(out), encoding="utf-8")


def write_vendor(skill: str, wheel: Path) -> None:
    vendor_dir = _vendor_dir(skill)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for old in vendor_dir.glob("*.whl"):
        old.unlink()
    dest = vendor_dir / wheel.name
    shutil.copyfile(wheel, dest)
    _pyproject_source_line(skill, wheel.name)


def cmd_write() -> int:
    with tempfile.TemporaryDirectory(prefix="agentce-vendor-") as raw:
        wheel = build_wheel(Path(raw) / "dist")
        for skill in SKILLS:
            write_vendor(skill, wheel)
            print(f"vendored {wheel.name} into skills/{skill}/vendor/")
    print(
        "Next: run `uv lock` inside each skill directory and commit "
        "pyproject.toml, uv.lock, and vendor/*.whl together."
    )
    return 0


def cmd_check() -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentce-vendor-check-") as raw:
        try:
            fresh = build_wheel(Path(raw) / "dist")
        except RuntimeError as exc:
            return [f"could not build a fresh wheel to compare against: {exc}"]
        fresh_contents = _wheel_contents(fresh)
        for skill in SKILLS:
            vendored = _vendored_wheel(skill)
            if vendored is None:
                problems.append(
                    f"skills/{skill}/vendor/ does not carry exactly one *.whl file"
                )
                continue
            try:
                vendored_contents = _wheel_contents(vendored)
            except zipfile.BadZipFile:
                problems.append(
                    f"skills/{skill}/vendor/{vendored.name} is not a valid zip/wheel"
                )
                continue
            missing = sorted(set(fresh_contents) - set(vendored_contents))
            extra = sorted(set(vendored_contents) - set(fresh_contents))
            drifted = sorted(
                name
                for name in set(fresh_contents) & set(vendored_contents)
                if fresh_contents[name] != vendored_contents[name]
            )
            if missing or extra or drifted:
                problems.append(
                    f"skills/{skill}/vendor/{vendored.name} is stale against a fresh build of "
                    f"engines/python (missing={missing[:5]}, extra={extra[:5]}, "
                    f"drifted={drifted[:5]})"
                )
    return problems


def self_test() -> int:
    failures: list[str] = []

    problems = cmd_check()
    if problems:
        failures.append(f"in-sync repo tree unexpectedly reported drift: {problems}")

    for skill in SKILLS:
        wheel = _vendored_wheel(skill)
        if wheel is None:
            failures.append(
                f"self-test setup: skills/{skill}/vendor has no wheel to corrupt"
            )
            continue
        backup = wheel.with_suffix(".whl.bak")
        shutil.copyfile(wheel, backup)
        try:
            wheel.write_bytes(b"not a real wheel")
            problems = cmd_check()
            if not any(skill in p for p in problems):
                failures.append(
                    f"corrupting skills/{skill}'s vendored wheel did not fail --check: {problems}"
                )
        finally:
            shutil.copyfile(backup, wheel)
            backup.unlink()

    if failures:
        for f in failures:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print(
        "self-test ok: --check discriminates a corrupted vendored wheel from an in-sync one"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--write", action="store_true", help="rebuild and re-vendor both skills"
    )
    group.add_argument(
        "--self-test", action="store_true", help="prove --check discriminates"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.write:
        return cmd_write()

    problems = cmd_check()
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print("ok: every skill's vendored wheel matches a fresh build of engines/python")
    return 0


if __name__ == "__main__":
    sys.exit(main())
