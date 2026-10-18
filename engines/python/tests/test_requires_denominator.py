"""``evaluation.requires_denominator`` (18.37e, DOC-03): the field is refused at load when malformed, never dropped,
and a verdict the records cannot reconcile with a declared independent denominator reads insufficient_evidence."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce.assess import _denominator_unshown
from agentce.catalog import ControlSpec, RequiredDenominator, load_catalog
from agentce.errors import InputError
from agentce.profile import CoverageDenominator, Subject

_EU = Path(__file__).resolve().parents[3] / "spec" / "catalogs" / "base" / "eu-ai-act"
_LINE = "  requires_denominator: {kinds: [egress_proxy_log, firewall_log], covers: ToolCall}\n"
SUBJECT = "spiffe://corp/agents/a"


def _control(control_id: str) -> ControlSpec:
    return next(c for c in load_catalog(_EU).controls if c.id == control_id)


def test_doc03_loads_its_declared_denominator() -> None:
    control = _control("DOC-03")
    assert control.requires_denominator == RequiredDenominator(
        frozenset({"egress_proxy_log", "firewall_log"}), "ToolCall"
    )


@pytest.mark.parametrize(
    "value",
    [
        "egress_proxy_log",
        "{kinds: [egress_proxy_log]}",
        "{kinds: [egress_proxy_log], covers: ToolCall, extra: 1}",
        "{kinds: [], covers: ToolCall}",
        "{kinds: [egress_log], covers: ToolCall}",
        "{kinds: [egress_proxy_log, egress_proxy_log], covers: ToolCall}",
        "{kinds: [egress_proxy_log], covers: ''}",
        "{kinds: [egress_proxy_log], covers: 7}",
        "{kinds: [7], covers: ToolCall}",
        "null",
    ],
    ids=[
        "not-object",
        "missing-covers",
        "extra-key",
        "empty-kinds",
        "unknown-kind",
        "repeated-kind",
        "empty-covers",
        "covers-not-string",
        "kind-not-string",
        "null",
    ],
)
def test_a_malformed_requirement_is_refused_at_load(tmp_path: Path, value: str) -> None:
    catalog_dir = tmp_path / "eu-ai-act"
    shutil.copytree(_EU, catalog_dir)
    control = catalog_dir / "controls" / "DOC-03.yaml"
    text = control.read_text(encoding="utf-8")
    assert _LINE in text
    control.write_text(
        text.replace(_LINE, f"  requires_denominator: {value}\n"), encoding="utf-8"
    )
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.control.requires_denominator_invalid"
    assert int(excinfo.value.exit_code) == 3
    assert "DOC-03" in excinfo.value.cause


def _coverage(**row: Any) -> dict[str, Any]:
    return {"subjects": {SUBJECT: {"event_types": {"ToolCall": row}}}}


def _subject(*kinds: str) -> Subject:
    return Subject(
        id=SUBJECT,
        coverage_denominators=[
            CoverageDenominator(
                kind=kind, source=f"urn:src:{kind}", covers=["ToolCall"]
            )
            for kind in kinds
        ],
    )


def test_the_verdict_needs_a_declared_denominator_whose_count_matches_the_capture() -> (
    None
):
    control = _control("DOC-03")
    egress = "urn:src:egress_proxy_log"
    shown = _coverage(status="covered", observed=2, expected=2, denominators=[egress])
    assert _denominator_unshown(control, _subject("egress_proxy_log"), shown) is None
    for subject, coverage in (
        (_subject(), shown),
        (_subject("system_of_record_export"), shown),
        (_subject("admission_attestation"), shown),
        (_subject("egress_proxy_log"), {"subjects": {}}),
        (
            _subject("egress_proxy_log"),
            _coverage(status="covered", observed=3, expected=2, denominators=[egress]),
        ),
        (
            _subject("egress_proxy_log"),
            _coverage(status="partial", observed=1, expected=2, denominators=[egress]),
        ),
        (
            _subject("egress_proxy_log", "system_of_record_export"),
            _coverage(
                status="covered",
                observed=2,
                expected=2,
                denominators=["urn:src:system_of_record_export"],
            ),
        ),
    ):
        assert _denominator_unshown(control, subject, coverage) is not None
    assert (
        _denominator_unshown(_control("OVS-06"), _subject(), {"subjects": {}}) is None
    )
