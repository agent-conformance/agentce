"""Repository check: the release-state switch is honest.

``governance/release-state.json`` lists every distribution channel as published or not. This check
keeps the rest of the repository consistent with it:

* the file is well formed: exactly the six known channels, each with a boolean ``published``, an
  artifact name, a ``today`` command, a ``one_liner`` and identifying ``patterns``; a channel that is
  published names the release version, and the version is the engine's;
* no tracked text presents the zero-install one-liner of a channel the switch marks unpublished (the
  data file and this checker excepted);
* with ``--online`` (the maintainer's verification after flipping a channel) every published channel
  really serves a runnable artifact: it is downloaded from the registry and run through the same
  installed-artifact check the pull-request guard uses, from an empty directory with the network cut.

Stdlib only; the default run needs no network.

Usage:
    release_state_check.py                    check the repository (exit 1 on any violation)
    release_state_check.py --online           also prove each published channel serves a runnable artifact
    release_state_check.py --command <chan>   print what an Install tab shows for the channel
    release_state_check.py --self-test        prove the checks discriminate on planted bad input
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATE = Path("governance/release-state.json")
CHANNELS = (
    "pypi",
    "npm",
    "maven-central",
    "container-registry",
    "homebrew-tap",
    "site",
)
# The state file and this checker hold the patterns themselves; the engine-docs test feeds sample page
# text (including npx commands) to the documentation checker, which is what it tests.
EXEMPT = frozenset(
    {
        str(STATE),
        "tools/release_state_check.py",
        "conformance/tests/test_engine_docs_check.py",
    }
)
SKIPPED_SUFFIXES = (
    ".lock",
    ".lockfile",
    ".png",
    ".jpg",
    ".ico",
    ".svg",
    ".woff2",
    ".jar",
)
SKIPPED_NAMES = frozenset({"pnpm-lock.yaml", "package-lock.json"})


def load_state(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / STATE).read_text(encoding="utf-8"))
    return data


def engine_version(root: Path) -> str:
    for line in (
        (root / "engines/python/pyproject.toml")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        if line.startswith("version = "):
            return line.split('"')[1]
    return ""


def schema_violations(state: dict[str, Any], version: str) -> list[str]:
    problems: list[str] = []
    if state.get("schema") != 1:
        problems.append("schema must be 1")
    channels = state.get("channels")
    if not isinstance(channels, dict) or set(channels) != set(CHANNELS):
        return [*problems, f"channels must be exactly {', '.join(CHANNELS)}"]
    if state.get("version") != version:
        problems.append(
            f"version {state.get('version')!r} is not the engine's {version!r}"
        )
    for name in CHANNELS:
        entry = channels[name]
        if not isinstance(entry.get("published"), bool):
            problems.append(f"{name}: published must be true or false")
        if not isinstance(entry.get("artifact"), str) or not entry["artifact"]:
            problems.append(f"{name}: artifact must name the distribution")
        for key in ("today", "one_liner"):
            if key not in entry or not (
                entry[key] is None or isinstance(entry[key], str)
            ):
                problems.append(f"{name}: {key} must be a string or null")
        patterns = entry.get("patterns")
        if not isinstance(patterns, list) or not all(
            isinstance(p, str) for p in patterns
        ):
            problems.append(f"{name}: patterns must be a list of regular expressions")
            continue
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(
                    f"{name}: pattern {pattern!r} is not a regular expression: {exc}"
                )
        if (
            entry.get("published") is True
            and entry.get("one_liner") is None
            and name != "site"
        ):
            problems.append(
                f"{name}: a published channel needs a one_liner or an explicit `today`"
            )
    return problems


def page_violations(state: dict[str, Any], files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, entry in state["channels"].items():
        if entry["published"]:
            continue
        compiled = [re.compile(p, re.I) for p in entry["patterns"]]
        for path, text in sorted(files.items()):
            for number, line in enumerate(text.splitlines(), 1):
                if any(rx.search(line) for rx in compiled):
                    problems.append(
                        f"{path}:{number}: shows a {name} one-liner but the channel is not published"
                    )
    return problems


def tracked_text(root: Path) -> dict[str, str]:
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    files: dict[str, str] = {}
    for rel in filter(None, listing.split("\0")):
        path = root / rel
        if (
            rel in EXEMPT
            or path.name in SKIPPED_NAMES
            or rel.endswith(SKIPPED_SUFFIXES)
        ):
            continue
        if not path.is_file():
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


def install_command(state: dict[str, Any], channel: str) -> str | None:
    """What an Install tab shows: the one-liner once published, the command that works today until then."""
    entry = state["channels"][channel]
    shown = entry["one_liner"] if entry["published"] else entry["today"]
    return shown if isinstance(shown, str) else None


# --- online verification: the artifact a registry serves must run --------------------------------


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https registry URLs
        data: bytes = response.read()
    return data


def _download(url: str, dest: Path) -> Path:
    dest.write_bytes(_get(url))
    return dest


def _verify_pypi(entry: dict[str, Any], version: str, tmp: Path) -> list[str]:
    import installed_artifacts_check as artifacts

    meta = json.loads(_get(f"https://pypi.org/pypi/{entry['artifact']}/{version}/json"))
    files = {f["packagetype"]: f for f in meta["urls"]}
    if "bdist_wheel" not in files or "sdist" not in files:
        return ["pypi: the release lacks a wheel or an sdist"]
    runnable = [
        (kind, _download(files[key]["url"], tmp / files[key]["filename"]))
        for kind, key in (("wheel", "bdist_wheel"), ("sdist", "sdist"))
    ]
    return artifacts.check_python_files(
        artifacts.Runner(False), runnable, offline_install=False
    )


def _verify_npm(entry: dict[str, Any], version: str, tmp: Path) -> list[str]:
    import installed_artifacts_check as artifacts

    name = entry["artifact"].replace("/", "%2f")
    meta = json.loads(_get(f"https://registry.npmjs.org/{name}/{version}"))
    tarball = _download(meta["dist"]["tarball"], tmp / "package.tgz")
    return artifacts.check_npm_tarball(
        artifacts.Runner(False), tarball, offline_install=False
    )


def _verify_maven(entry: dict[str, Any], version: str, tmp: Path) -> list[str]:
    import installed_artifacts_check as artifacts

    group, artifact = entry["artifact"].split(":")
    base = (
        f"https://repo1.maven.org/maven2/{group.replace('.', '/')}/{artifact}/{version}"
    )
    jar = _download(
        f"{base}/{artifact}-{version}-all.jar", tmp / f"{artifact}-{version}-all.jar"
    )
    return artifacts.check_jar_file(artifacts.Runner(False), jar)


def _verify_container(entry: dict[str, Any], version: str, tmp: Path) -> list[str]:
    image = f"{entry['artifact']}:{version}"
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            image,
            "quickstart",
            "--out",
            "/tmp/report",
        ],
        capture_output=True,
        text=True,
    )
    return (
        []
        if proc.returncode == 0
        else [f"container-registry: {image} did not run quickstart offline"]
    )


def _verify_brew(entry: dict[str, Any], version: str, tmp: Path) -> list[str]:
    install = subprocess.run(
        ["brew", "install", entry["artifact"]], capture_output=True, text=True
    )
    if install.returncode != 0:
        return ["homebrew-tap: brew install failed"]
    run = subprocess.run(
        ["agentce", "--version"], capture_output=True, text=True, cwd=tmp
    )
    return (
        []
        if run.stdout.strip() == f"agentce {version}"
        else ["homebrew-tap: agentce --version is wrong"]
    )


def _verify_site(entry: dict[str, Any], version: str, tmp: Path) -> list[str]:
    body = _get(entry["artifact"]).decode("utf-8", errors="replace")
    return [] if "<html" in body.lower() else ["site: the root page is not HTML"]


VERIFIERS = {
    "pypi": _verify_pypi,
    "npm": _verify_npm,
    "maven-central": _verify_maven,
    "container-registry": _verify_container,
    "homebrew-tap": _verify_brew,
    "site": _verify_site,
}


def online_violations(state: dict[str, Any]) -> list[str]:
    sys.path.insert(0, str(ROOT / "tools"))
    problems: list[str] = []
    for name, entry in state["channels"].items():
        if not entry["published"]:
            continue
        with tempfile.TemporaryDirectory(prefix="agentce-release-state-") as raw:
            try:
                problems += VERIFIERS[name](entry, state["version"], Path(raw))
            except (
                Exception
            ) as exc:  # a registry that does not answer is a failed proof, not a crash
                problems.append(f"{name}: could not verify: {exc}")
    return problems


def violations(root: Path, *, online: bool) -> list[str]:
    state = load_state(root)
    problems = schema_violations(state, engine_version(root))
    if problems:
        return problems
    problems += page_violations(state, tracked_text(root))
    if online:
        problems += online_violations(state)
    return problems


# --- self-test -----------------------------------------------------------------------------------


def _fixture_state() -> dict[str, Any]:
    state = load_state(ROOT)
    assert isinstance(state, dict)
    return state


def self_test() -> int:
    failures: list[str] = []
    good = _fixture_state()
    version = engine_version(ROOT)
    if schema_violations(good, version):
        failures.append(
            f"the real state file is invalid: {schema_violations(good, version)}"
        )

    def flag(problems: list[str], needle: str, case: str) -> None:
        if not any(needle in p for p in problems):
            failures.append(
                f"{case}: expected a violation mentioning '{needle}', got {problems}"
            )

    bad = json.loads(json.dumps(good))
    del bad["channels"]["homebrew-tap"]
    flag(schema_violations(bad, version), "exactly", "missing channel")
    bad = json.loads(json.dumps(good))
    bad["channels"]["pypi"]["published"] = "yes"
    flag(schema_violations(bad, version), "pypi: published", "non-boolean published")
    bad = json.loads(json.dumps(good))
    bad["version"] = "9.9.9"
    flag(schema_violations(bad, version), "version", "version drift")
    bad = json.loads(json.dumps(good))
    bad["channels"]["npm"]["patterns"] = ["("]
    flag(schema_violations(bad, version), "not a regular expression", "broken pattern")

    pages = {
        "site/install.md": "Run `pipx run agent-conformance` to start.\n",
        "site/tab.md": "$ npx --package @agent-conformance/cli agentce --version\n",
        "site/brew.md": "brew install agent-conformance/tap/agentce\n",
        "site/box.md": "docker run --rm ghcr.io/agent-conformance/agentce:0.1.0 quickstart\n",
        "site/jar.md": "implementation 'org.agentce:agentce:0.1.0'\n",
        "site/ok.md": "brew install openssl@3\ndocker run --rm agentce/agentce:dev quickstart\nuv run agentce quickstart\n",
    }
    found = page_violations(good, pages)
    for path in ("install.md", "tab.md", "brew.md", "box.md", "jar.md"):
        flag(found, f"site/{path}", f"unpublished one-liner in {path}")
    if any("ok.md" in p for p in found):
        failures.append(f"a page with no one-liner was flagged: {found}")

    flipped = json.loads(json.dumps(good))
    flipped["channels"]["pypi"]["published"] = True
    if any("install.md" in p for p in page_violations(flipped, pages)):
        failures.append("a published channel's one-liner was flagged")
    if not any("brew.md" in p for p in page_violations(flipped, pages)):
        failures.append("flipping pypi also excused the brew one-liner")

    if install_command(good, "pypi") != good["channels"]["pypi"]["today"]:
        failures.append(
            "an unpublished channel shows something other than the command that works today"
        )
    if install_command(flipped, "pypi") != good["channels"]["pypi"]["one_liner"]:
        failures.append("a published channel does not show its one-liner")
    if install_command(good, "homebrew-tap") is not None:
        failures.append("a channel with nothing that works today shows a command")

    # The verifier for a published channel must fail loudly when the registry does not serve it.
    unreachable = json.loads(json.dumps(good))
    unreachable["channels"]["site"]["published"] = True
    unreachable["channels"]["site"]["artifact"] = "http://127.0.0.1:9/"
    flag(
        online_violations(unreachable),
        "site: could not verify",
        "unreachable published channel",
    )
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("release_state_check self-test: 8 cases discriminate")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_state_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="prove each published channel serves a runnable artifact",
    )
    parser.add_argument(
        "--command",
        choices=CHANNELS,
        help="print what an Install tab shows for the channel",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command:
        shown = install_command(load_state(ROOT), args.command)
        print(shown if shown is not None else "")
        return 0
    problems = violations(ROOT, online=args.online)
    for problem in problems:
        print(f"VIOLATION: {problem}", file=sys.stderr)
    if not problems:
        print("release-state: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
