"""The engine's no-learned-components self-report and its denylist (SPEC §8.7)."""

from __future__ import annotations

from pathlib import Path

from agentce import no_ml

_HERE = Path(__file__).resolve()
_ENGINE_DIR = _HERE.parents[1]
_REPO_ROOT = _HERE.parents[3]
_VENDORED = _ENGINE_DIR / "agentce" / "data" / "no-ml-denylist.txt"
_AUTHORITATIVE = _REPO_ROOT / "spec" / "rules" / "no-ml-denylist.txt"


def test_vendored_denylist_matches_authoritative_copy() -> None:
    # The engine vendors a copy so it can self-report when installed; a drift would let a fork
    # slip a learned component past the self-report, so it must stay byte-identical.
    assert _VENDORED.read_text(encoding="utf-8") == _AUTHORITATIVE.read_text(
        encoding="utf-8"
    )


def test_normalise_pep503() -> None:
    assert no_ml.normalise("Scikit_Learn") == "scikit-learn"
    assert no_ml.normalise("google.generativeai") == "google-generativeai"
    assert no_ml.normalise("PyYAML") == "pyyaml"


def test_load_denylist_has_expected_entries() -> None:
    denylist = no_ml.load_denylist()
    assert {"torch", "tensorflow", "openai", "transformers"} <= denylist
    # Names that merely contain "ml" are unaffected (compared by exact normalised name).
    assert "linkml" not in denylist
    assert "pyyaml" not in denylist


def test_evaluate_passes_in_clean_environment() -> None:
    result = no_ml.evaluate()
    assert result["result"] == "pass"
    assert result["denylisted_present"] == []
