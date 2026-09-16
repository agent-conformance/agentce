"""Implementation-report registry gate (SPEC §11.5, §14.5 CP-1, P5.6): a listing requires a golden
revision that verifies against its signature; a report whose golden digest does not verify is refused.
"""

from __future__ import annotations

import registry_check
from agentce import signing


def test_self_test_passes() -> None:
    assert registry_check.self_test() == 0


def test_well_formed_report_is_listed() -> None:
    trust = signing.vendored_trust()
    digest = "sha256:" + "0" * 64
    result = registry_check.evaluate_report(
        registry_check._sample_report(digest), trust
    )
    assert result["listed"] is True
    assert result["status"] == "listed"


def test_tampered_golden_digest_is_unverified() -> None:
    trust = signing.vendored_trust()
    report = registry_check._sample_report("sha256:" + "0" * 64)
    report["golden"]["digest"] = "sha256:" + "1" * 64
    result = registry_check.evaluate_report(report, trust)
    assert result["listed"] is False
    assert result["status"] == "unverified"


def test_missing_signature_is_unverified() -> None:
    trust = signing.vendored_trust()
    report = registry_check._sample_report("sha256:" + "0" * 64)
    del report["golden"]["signature"]
    result = registry_check.evaluate_report(report, trust)
    assert result["listed"] is False
    assert result["status"] == "unverified"


def test_partial_claim_is_not_conforming() -> None:
    trust = signing.vendored_trust()
    report = registry_check._sample_report("sha256:" + "0" * 64)
    report["claim"] = "partial"
    result = registry_check.evaluate_report(report, trust)
    assert result["listed"] is False
    assert result["status"] == "not-conforming"
