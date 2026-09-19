"""Engine configuration with an explicit precedence and source for every value (SPEC §8.5, §13.4).

An adopter sets a handful of engine options; ``agentce config show`` reports the resolved value of each
together with *where* it came from, so a surprising setting is traceable. Precedence, lowest to
highest: the built-in default, an ``agentce.toml`` file (its ``[config]`` table), an ``AGENTCE_<KEY>``
environment variable, then a command-line flag. Every value therefore has a non-empty source — a
built-in default at least (AX-8).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConfigKey:
    key: str
    default: Any
    cast: Callable[[Any], Any]
    help: str


#: The Phase-1 configuration surface. Each key is grounded in an engine behaviour it tunes.
CONFIG_KEYS: tuple[ConfigKey, ...] = (
    ConfigKey(
        "report_language", "en", str, "language of the human report (SPEC §4.10)"
    ),
    ConfigKey(
        "clock_skew_seconds",
        300,
        int,
        "allowed source-clock skew for timestamp trust (SPEC §6.6)",
    ),
    ConfigKey(
        "max_event_bytes",
        1_048_576,
        int,
        "maximum size of one event line before quarantine (SPEC App. F)",
    ),
    ConfigKey(
        "evidence_cap", 20, int, "maximum evidence pointers cited per assertion (DC-5)"
    ),
    ConfigKey(
        "pseudonymisation", "none", str, "principal pseudonymisation key source (IR-12)"
    ),
    ConfigKey(
        "operator",
        "unset",
        str,
        "run.operator recorded in manifest.json (SPEC §8.4) — a string the deployer "
        "controls via AGENTCE_OPERATOR or agentce.toml; never the invoking OS user by default",
    ),
)

_KEYS_BY_NAME = {ck.key: ck for ck in CONFIG_KEYS}


def _env_name(key: str) -> str:
    return "AGENTCE_" + key.upper()


def _load_file(path: Path) -> dict[str, Any]:
    """Read an ``agentce.toml``'s ``[config]`` table (or its top level); missing file → no values."""
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    table = data.get("config", data)
    return (
        {k: v for k, v in table.items() if k in _KEYS_BY_NAME}
        if isinstance(table, dict)
        else {}
    )


def _discover_file(start: Path | None) -> Path | None:
    candidate = (start or Path.cwd()) / "agentce.toml"
    return candidate if candidate.is_file() else None


def resolve(
    *,
    config_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    flags: Mapping[str, Any] | None = None,
    cwd: Path | None = None,
) -> list[dict[str, Any]]:
    """Resolve every config key to ``{key, value, source}`` under the precedence order."""
    env = os.environ if env is None else env
    flags = flags or {}
    chosen = config_file or _discover_file(cwd)
    file_values = _load_file(chosen) if chosen is not None else {}

    resolved: list[dict[str, Any]] = []
    for ck in CONFIG_KEYS:
        value: Any = ck.default
        source = "default"
        if ck.key in file_values:
            value, source = ck.cast(file_values[ck.key]), "file"
        env_name = _env_name(ck.key)
        if env_name in env:
            value, source = ck.cast(env[env_name]), "env"
        if flags.get(ck.key) is not None:
            value, source = ck.cast(flags[ck.key]), "flag"
        resolved.append({"key": ck.key, "value": value, "source": source})
    return resolved
