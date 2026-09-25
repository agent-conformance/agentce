"""Offline distribution bundle and the governance gate (SPEC §14.3, §14.5, P5.6).

The bundle builds and then verifies with networking disabled: signatures verify offline and the ECS
runs on the bundled corpus project. The governance gate reports all six conditions true.
"""

from __future__ import annotations

import json
import shutil
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

    # Multi-catalog: every signed base catalog is discovered and bundled, not just eu-ai-act (P16.7).
    manifest = json.loads((out / "bundle-manifest.json").read_text(encoding="utf-8"))
    catalog_ids = {entry["id"] for entry in manifest["catalogs"]}
    assert catalog_ids == {"eu-ai-act", "nist-ai-rmf"}
    assert (out / "catalogs" / "nist-ai-rmf" / "catalog.sig.json").is_file()
    # The singular field stays byte-identical for back-compat: the same eu-ai-act entry.
    eu_ai_act = next(e for e in manifest["catalogs"] if e["id"] == "eu-ai-act")
    assert manifest["catalog"] == eu_ai_act

    result = offline_bundle.verify_bundle(out, no_network=True)
    assert result["problems"] == []
    assert result["ran_ecs"] is True


def test_tampered_second_catalog_fails_verify_with_named_problem(
    tmp_path: Path,
) -> None:
    """A discovered-at-build-time catalog whose bundled copy is tampered must fail loudly and by
    name -- never a silent whole-bundle OK that only happens to have checked eu-ai-act."""
    out = tmp_path / "bundle"
    offline_bundle.build_bundle(out)

    nist_sig = out / "catalogs" / "nist-ai-rmf" / "catalog.sig.json"
    envelope = json.loads(nist_sig.read_text(encoding="utf-8"))
    envelope["signatures"][0]["sig"] = "AAAA" + envelope["signatures"][0]["sig"][4:]
    nist_sig.write_text(json.dumps(envelope), encoding="utf-8")

    result = offline_bundle.verify_bundle(out, no_network=True)
    assert result["problems"], (
        "a tampered second catalog must produce a problem, not a silent OK"
    )
    assert any("nist-ai-rmf" in problem for problem in result["problems"]), result[
        "problems"
    ]


def test_missing_second_catalog_fails_verify_with_named_problem(tmp_path: Path) -> None:
    """A catalog the manifest names as discovered but that is absent at verify time must fail by
    name, not be silently skipped."""
    out = tmp_path / "bundle"
    offline_bundle.build_bundle(out)

    shutil.rmtree(out / "catalogs" / "nist-ai-rmf")

    result = offline_bundle.verify_bundle(out, no_network=True)
    assert result["problems"], (
        "a missing second catalog must produce a problem, not a silent OK"
    )
    assert any("nist-ai-rmf" in problem for problem in result["problems"]), result[
        "problems"
    ]


def test_governance_gate_all_true() -> None:
    result = governance_check.run()
    assert all(result.values()), result


def test_registry_refuses_reports_that_do_not_verify() -> None:
    assert governance_check._registry_refuses() is True


def test_signing_verifies_offline() -> None:
    assert governance_check._signing_offline() is True
