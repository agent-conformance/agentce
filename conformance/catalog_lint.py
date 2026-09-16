"""Phase-4 P4.1 orchestrator: catalog and overlay lint (SPEC §7.3, §7.4, §7.7).

``python catalog_lint.py --json`` prints
``{"base_controls", "lint_clean", "cnd_controls", "cnd_fixtures", "weakening_refused"}``:
the base catalog has at least 45 controls and lints clean along with the overlays, the Conduct
overlay ships its eight CND controls with fixtures, and an overlay that weakens a base control is
refused by the lint.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "engines" / "python"))

from agentce.catalog import (  # noqa: E402
    ControlSpec,
    lint_catalog,
    load_catalog,
    overlay_weakens_base,
)

_BASE = _REPO / "spec" / "catalogs" / "base" / "eu-ai-act"
_OVERLAYS = _REPO / "spec" / "catalogs" / "overlays"
_CONDUCT = _OVERLAYS / "conduct"


def _lint_clean() -> bool:
    if lint_catalog(_BASE, require_verification_flags=True):
        return False
    return all(
        not lint_catalog(catalog_yaml.parent)
        for catalog_yaml in sorted(_OVERLAYS.rglob("catalog.yaml"))
    )


def _cnd_fixtures_present(conduct_controls: list[ControlSpec]) -> bool:
    for control in conduct_controls:
        for case in control.test_cases:
            if not (_CONDUCT / case["fixture"]).is_file():
                return False
    return True


def _weakening_refused(base_controls: list[ControlSpec]) -> bool:
    # A hostile overlay that re-uses a base control id with a lower severity and a looser tolerance.
    base = next(c for c in base_controls if c.severity == "high")
    weakening = ControlSpec(
        id=base.id,
        version=base.version,
        title="weakened",
        applies_to_roles=["both"],
        mode="automated",
        rung=2,
        severity="low",
        min_source_class="self_report",
        minimum_evidence=[],
        shape_path=None,
        tolerance={"kind": "ratio", "max": "0.5"},
        test_cases=[],
    )
    return bool(overlay_weakens_base(base_controls, [weakening]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-4 P4.1 catalog/overlay lint gate."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    base = load_catalog(_BASE)
    conduct = load_catalog(_CONDUCT)
    cnd = [c for c in conduct.controls if c.id.startswith("CND-")]
    result = {
        "base_controls": len(base.controls),
        "lint_clean": _lint_clean(),
        "cnd_controls": len(cnd),
        "cnd_fixtures": _cnd_fixtures_present(cnd),
        "weakening_refused": _weakening_refused(base.controls),
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for key, value in sorted(result.items()):
            print(f"{key}={value}")
    ok = (
        result["base_controls"] >= 45
        and result["lint_clean"]
        and result["cnd_controls"] == 8
        and result["cnd_fixtures"]
        and result["weakening_refused"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
