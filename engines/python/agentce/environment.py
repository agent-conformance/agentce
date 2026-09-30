"""Interpreter and toolchain preflight for ``agentce doctor`` (SPEC §13.4 AX-5).

The engine signs and verifies with Ed25519 through ``cryptography``. That package publishes prebuilt
wheels for Linux (glibc and musl), Windows x86_64, and macOS arm64 for every release since 49.0.0; no
release since then ships an Intel-macOS wheel (ADR-0024). ``engines/python/pyproject.toml`` and
``adapters/supply-chain/pyproject.toml`` therefore pin darwin/x86_64 to the 48.x line instead, whose
universal2 wheel (tagged ``macosx_10_9_universal2``) installs on Intel Macs as a real prebuilt
binary, not a source build. Installing outside that matched set (for example a bare ``macosx_*_x86_64``
tag, which no release publishes) still compiles from source and needs a Rust toolchain and an OpenSSL 3
build. This module reads what the running interpreter actually has installed and reports it, so a user
learns what is missing from ``agentce doctor`` instead of from a build failure.

Nothing here is a constant standing in for a measurement: the interpreter version comes from
``sys.version_info``, and the wheel provenance from the installed distribution's own ``WHEEL`` file.
"""

from __future__ import annotations

import platform
import re
import sys
from importlib import metadata
from typing import Any

#: The oldest interpreter the engine supports (``requires-python`` in ``pyproject.toml``).
MINIMUM_PYTHON = (3, 12)

#: Platform-tag prefixes for which ``cryptography`` publishes a prebuilt wheel: Linux and Windows.
PREBUILT_PLATFORM_PREFIXES = (
    "manylinux",
    "musllinux",
    "win_amd64",
)

#: macOS platform tags ``cryptography`` publishes a prebuilt wheel for, matched by architecture rather
#: than by a fixed deployment-target version (that version has moved once already, arm64's 10.9 base
#: through 11.0, and will again): ``arm64`` on the 49.x line onward, and the ``universal2`` fat binary
#: that also covers Intel macOS through the 48.x line (ADR-0024). A future release bumping either
#: version number keeps matching; only a new architecture tag needs a code change.
PREBUILT_MACOS_TAG = re.compile(r"^macosx_\d+_\d+_(arm64|universal2)$")

SOURCE_BUILD_FIX = (
    "install Rust (https://rustup.rs) and OpenSSL 3 before syncing: on macOS `brew install openssl@3` "
    'then `export OPENSSL_DIR="$(brew --prefix openssl@3)"`; on Linux install the OpenSSL 3 '
    "development headers. Then reinstall so the pinned cryptography builds."
)


def wheel_source(tags: list[str]) -> str:
    """``prebuilt`` when every installed platform tag is one ``cryptography`` publishes a wheel for,
    else ``source-build`` (also the answer when the distribution recorded no tag)."""
    if not tags:
        return "source-build"
    for tag in tags:
        platform_tag = tag.rsplit("-", 1)[-1]
        if not platform_tag.startswith(
            PREBUILT_PLATFORM_PREFIXES
        ) and not PREBUILT_MACOS_TAG.match(platform_tag):
            return "source-build"
    return "prebuilt"


def _wheel_tags(dist: metadata.Distribution) -> list[str]:
    text = dist.read_text("WHEEL") or ""
    return [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Tag:")
    ]


def _openssl_version() -> str | None:
    try:
        from cryptography.hazmat.backends.openssl.backend import backend

        return str(backend.openssl_version_text())
    except Exception:
        return None


def inspect_environment() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Describe the running interpreter and the installed ``cryptography``.

    Returns the ``environment`` section for ``doctor``'s output and the problems it implies (an
    interpreter below the minimum, or a ``cryptography`` that is absent or will not import).
    Advisory findings (a source build) travel in the section's ``notes``, not as problems: a source
    build works, it is only worth knowing about.
    """
    problems: list[dict[str, str]] = []
    notes: list[dict[str, str]] = []
    info = sys.version_info
    minimum = ".".join(str(part) for part in MINIMUM_PYTHON)
    python = {
        "version": f"{info.major}.{info.minor}.{info.micro}",
        "implementation": platform.python_implementation(),
        "minimum": minimum,
        "supported": (info.major, info.minor) >= MINIMUM_PYTHON,
    }
    if not python["supported"]:
        problems.append(
            {
                "key": "environment.python_unsupported",
                "problem": f"Python {python['version']} is older than the supported {minimum}",
                "fix": f"run the engine under Python {minimum} or newer (`uv python install {minimum}`).",
            }
        )

    crypto: dict[str, Any]
    try:
        dist = metadata.distribution("cryptography")
        tags = _wheel_tags(dist)
        source = wheel_source(tags)
        crypto = {
            "version": dist.version,
            "wheel_tags": tags,
            "wheel_source": source,
            "openssl": _openssl_version(),
        }
        if crypto["openssl"] is None:
            problems.append(
                {
                    "key": "environment.cryptography_unavailable",
                    "problem": f"cryptography {dist.version} is installed but does not import",
                    "fix": f"reinstall it from a clean environment; {SOURCE_BUILD_FIX}",
                }
            )
        if source == "source-build":
            notes.append(
                {
                    "key": "environment.cryptography_source_build",
                    "note": "cryptography "
                    f"{dist.version} was built from source: no prebuilt wheel is published for "
                    f"{platform.system()} {platform.machine()}",
                    "fix": SOURCE_BUILD_FIX,
                }
            )
    except metadata.PackageNotFoundError:
        crypto = {
            "version": None,
            "wheel_tags": [],
            "wheel_source": None,
            "openssl": None,
        }
        problems.append(
            {
                "key": "environment.cryptography_unavailable",
                "problem": "the cryptography package is not installed in this environment",
                "fix": "install the engine's dependencies (`uv sync`); on a platform with no "
                f"prebuilt wheel, {SOURCE_BUILD_FIX}",
            }
        )

    section = {
        "python": python,
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "cryptography": crypto,
        "notes": notes,
    }
    return section, problems
