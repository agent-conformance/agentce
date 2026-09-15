"""The CommandResult envelope and its exit-code derivation."""

from __future__ import annotations

from agentce.exit_codes import ExitCode
from agentce.result import CommandResult


def test_default_result_is_ok() -> None:
    result = CommandResult(command="x")
    assert result.exit_code == int(ExitCode.OK)
    assert result.applicable_codes == [0]
    env = result.envelope()
    assert env["command"] == "x"
    assert env["exit_code"] == 0
    assert env["exit_status"] == ["ok"]


def test_data_is_flattened_at_top_level() -> None:
    result = CommandResult(command="x", data={"foo": "bar"})
    env = result.envelope()
    assert env["foo"] == "bar"


def test_reserved_keys_override_data() -> None:
    result = CommandResult(command="x", data={"exit_code": 999, "command": "spoof"})
    env = result.envelope()
    assert env["exit_code"] == 0
    assert env["command"] == "x"


def test_note_accumulates_human_lines() -> None:
    result = CommandResult(command="x")
    result.note("first")
    result.note("second")
    assert result.human_lines == ["first", "second"]


def test_highest_code_wins_and_all_are_listed() -> None:
    result = CommandResult(command="x")
    result.add_code(int(ExitCode.INPUT_ERROR))
    result.add_code(int(ExitCode.FINDINGS))
    assert result.exit_code == 3
    assert result.applicable_codes == [1, 3]
