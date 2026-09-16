"""The Phase-3 probe-evaluation conformance check over the frozen corpus (P3.2)."""

from __future__ import annotations

import probe


def test_all_oracles_agree_and_tamper_is_rejected() -> None:
    result = probe.run()
    assert result["oracles_passed"] is True
    assert result["hash_mismatch_rejected"] is True
    assert len(result["probes"]) == 4
    assert all(entry["agrees"] for entry in result["probes"])


def test_main_exits_zero() -> None:
    assert probe.main(["--json"]) == 0
