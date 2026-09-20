"""The cross-language numerics gate (SPEC §7.2, P3.1): every vector passes in both engines, identical."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numerics
import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("pnpm") is None, reason="the TypeScript engine (pnpm) is not available"
)


def test_vectors_pass_in_both_engines_identically() -> None:
    result = numerics.run()
    assert result["total"] > 0
    assert result["python_failed"] == 0
    assert result["ts_failed"] == 0
    assert result["identical"] is True


def test_main_json_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = numerics.main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"identical": true' in out


@pytest.mark.skipif(
    shutil.which("java") is None, reason="the Java engine (a JDK) is not available"
)
def test_edge_vectors_pass_in_all_three_engines_identically() -> None:
    result = numerics.run(("python", "typescript", "java"))
    assert result["total"] > 0
    assert result["python_failed"] == 0
    assert result["ts_failed"] == 0
    assert result["java_failed"] == 0
    assert result["identical"] is True


def test_engines_flag_must_keep_the_two_reference_engines() -> None:
    with pytest.raises(SystemExit):
        numerics.main(["--engines", "java"])
    with pytest.raises(SystemExit):
        numerics.main(["--engines", "python,typescript,rust"])


def test_a_wrong_expectation_is_counted_as_a_failure_in_every_engine(
    tmp_path: Path,
) -> None:
    # 2**53 and 2**53 + 1 are the same IEEE-754 double: an engine that compared through floating point
    # would call them equal, so expecting "eq" must fail exactly where the ordering is exact.
    cases = tmp_path / "cases"
    shutil.copytree(numerics.CASES, cases)
    edge = cases / "edge-comparison.json"
    data = json.loads(edge.read_text(encoding="utf-8"))
    planted = next(c for c in data["cases"] if c["name"] == "int-2^53-below-2^53+1")
    planted["expected"] = "eq"
    edge.write_text(json.dumps(data), encoding="utf-8")
    result = numerics.run(("python", "typescript"), cases)
    assert result["python_failed"] == 1
    assert result["ts_failed"] == 1
