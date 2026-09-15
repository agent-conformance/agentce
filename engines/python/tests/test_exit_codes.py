"""The exit-code table: the common scheme of SPEC §8.5 and its behaviour at the CLI boundary.

This is the test the phase-1 eval selects for `P1.7` (it runs `pytest -k "exit_code or exit_codes"`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentce import cli
from agentce.exit_codes import ExitCode, applicable, combine, name_of
from agentce.result import CommandResult

VALID = [int(c) for c in ExitCode]


def test_exit_code_values_and_names() -> None:
    assert VALID == [0, 1, 2, 3]
    assert name_of(0) == "ok"
    assert name_of(1) == "findings"
    assert name_of(2) == "insufficient_evidence"
    assert name_of(3) == "input_error"


def test_exit_code_name_of_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        name_of(9)


def test_exit_code_combine_highest_wins() -> None:
    assert combine([]) == 0
    assert combine([0]) == 0
    assert combine([1, 3, 2]) == 3
    assert combine([1, 2]) == 2
    assert combine([0, 1]) == 1


def test_exit_code_combine_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        combine([7])


@given(st.lists(st.sampled_from(VALID)))
def test_exit_code_combine_is_order_independent_max(codes: list[int]) -> None:
    assert combine(codes) == combine(list(reversed(codes)))
    assert combine(codes) == (max(codes) if codes else 0)


def test_exit_codes_applicable_reports_ok_only_when_alone() -> None:
    assert applicable([]) == [0]
    assert applicable([0]) == [0]
    assert applicable([1, 1, 2]) == [1, 2]
    assert applicable([0, 1]) == [1]


def test_exit_codes_json_carries_all_applicable() -> None:
    result = CommandResult(command="x")
    result.add_code(int(ExitCode.FINDINGS))
    result.add_code(int(ExitCode.INSUFFICIENT_EVIDENCE))
    env = result.envelope()
    assert env["exit_code"] == 2
    assert env["exit_codes"] == [1, 2]
    assert env["exit_status"] == ["findings", "insufficient_evidence"]


def test_exit_code_add_code_rejects_unknown() -> None:
    result = CommandResult(command="x")
    with pytest.raises(ValueError):
        result.add_code(9)


def _run(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_exit_code_cli_success_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = _run(["version", "--json"], capsys)
    assert code == 0
    assert env["exit_code"] == 0
    assert env["exit_status"] == ["ok"]


def test_exit_code_cli_missing_input_is_three(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, env = _run(["validate", "--json"], capsys)  # no --bundle
    assert code == 3
    assert env["exit_code"] == 3
    assert env["error"]["key"].startswith("input.")


def test_exit_code_cli_usage_error_is_three() -> None:
    # argparse rejects an unknown flag; the scheme maps that to 3, not argparse's default 2.
    assert cli.main(["validate", "--no-such-flag"]) == 3


def test_exit_code_cli_help_is_zero() -> None:
    assert cli.main(["--help"]) == 0
