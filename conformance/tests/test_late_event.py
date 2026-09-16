"""The late-event / supersession gate (SPEC §9.6, HR-10, P3.6): a late event produces supersedes."""

from __future__ import annotations

import late_event


def test_late_event_produces_supersedes() -> None:
    result = late_event.compute_late_event()
    assert result["supersedes_produced"] is True
    assert result["supersedes"], "the second report must list the first in supersedes"
    assert (
        result["first_report_supersedes"] == []
    )  # the first report supersedes nothing


def test_main_json_reports_supersedes(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = late_event.main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"supersedes_produced": true' in out
