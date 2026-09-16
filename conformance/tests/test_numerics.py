"""The cross-language numerics gate (SPEC §7.2, P3.1): every vector passes in both engines, identical."""

from __future__ import annotations

import shutil

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
