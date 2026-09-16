"""Offline distribution bundle and the governance gate (SPEC §14.3, §14.5, P5.6).

The bundle builds and then verifies with networking disabled: signatures verify offline and the ECS
runs on the bundled corpus project. The governance gate reports all six conditions true.
"""

from __future__ import annotations

from pathlib import Path

import governance_check
import offline_bundle


def test_bundle_builds_and_verifies_offline(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    offline_bundle.build_bundle(out)
    # The installable set, the signed catalog, the trust root, and a corpus project are all present.
    assert (out / "sbom.cdx.json").is_file()
    assert (out / "openvex.json").is_file()
    assert (out / "catalogs" / "eu-ai-act" / "catalog.sig.json").is_file()
    assert (out / "trust" / "dev-root.json").is_file()
    assert (out / "project" / "evidence").is_dir()

    result = offline_bundle.verify_bundle(out, no_network=True)
    assert result["problems"] == []
    assert result["ran_ecs"] is True


def test_governance_gate_all_true() -> None:
    result = governance_check.run()
    assert all(result.values()), result


def test_registry_refuses_reports_that_do_not_verify() -> None:
    assert governance_check._registry_refuses() is True


def test_signing_verifies_offline() -> None:
    assert governance_check._signing_offline() is True
