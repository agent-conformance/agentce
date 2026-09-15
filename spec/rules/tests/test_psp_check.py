"""Behavioural tests for psp_check (SPEC 7.2 Portable Shape Profile).

The checker's output strings and exit codes are a stable contract: the conformance harness reads
"PSP OK" / "REFUSED: sh:sparql" and the exit code. These tests pin that contract and cover the
profile boundaries the two phase-0 fixtures do not: unbounded paths, over-long sequences, non-literal
patterns, logical combinators, and range constraints on unsupported datatypes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psp_check

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent / "examples"

PREFIXES = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def run(argv: list[str], capsys) -> tuple[int, str]:
    rc = psp_check.main(argv)
    out = capsys.readouterr()
    return rc, (out.out + out.err)


def write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "shape.ttl"
    p.write_text(PREFIXES + body, encoding="utf-8")
    return str(p)


def test_appendix_b_example_is_within_profile(capsys):
    rc, out = run([str(EXAMPLES / "OVS-03.ttl")], capsys)
    assert rc == 0
    assert "PSP OK" in out


def test_sparql_shape_is_refused_naming_sparql(capsys):
    rc, out = run([str(EXAMPLES / "non-portable-sparql.ttl")], capsys)
    assert rc == 1
    assert "REFUSED: sh:sparql" in out


def test_refusal_is_deterministic_across_runs(capsys):
    shape = str(EXAMPLES / "non-portable-sparql.ttl")
    rc1, out1 = run([shape], capsys)
    rc2, out2 = run([shape], capsys)
    assert (rc1, out1) == (rc2, out2)


def test_zero_or_more_path_is_refused(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        "  sh:property [ sh:path [ sh:zeroOrMorePath agentce:derivedFrom ] ; sh:minCount 1 ] .\n",
    )
    rc, out = run([shape], capsys)
    assert rc == 1
    assert "REFUSED: sh:zeroOrMorePath" in out


def test_sequence_path_over_three_is_refused(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        "  sh:property [ sh:path ( agentce:a agentce:b agentce:c agentce:d ) ; sh:minCount 1 ] .\n",
    )
    rc, out = run([shape], capsys)
    assert rc == 1
    assert "REFUSED:" in out and "sequence of 4" in out


def test_sequence_path_of_three_is_allowed(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        "  sh:property [ sh:path ( agentce:a agentce:b agentce:c ) ; sh:minCount 1 ] .\n",
    )
    rc, out = run([shape], capsys)
    assert rc == 0
    assert "PSP OK" in out


def test_non_literal_pattern_is_refused(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        '  sh:property [ sh:path agentce:name ; sh:pattern "^tool-[0-9]+" ] .\n',
    )
    rc, out = run([shape], capsys)
    assert rc == 1
    assert "REFUSED: sh:pattern" in out


def test_anchored_literal_pattern_is_allowed(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        '  sh:property [ sh:path agentce:name ; sh:pattern "^urn:agentce:" ] .\n',
    )
    rc, out = run([shape], capsys)
    assert rc == 0
    assert "PSP OK" in out


def test_logical_combinator_is_refused(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        "  sh:or ( [ sh:path agentce:a ; sh:minCount 1 ] [ sh:path agentce:b ; sh:minCount 1 ] ) .\n",
    )
    rc, out = run([shape], capsys)
    assert rc == 1
    assert "REFUSED: sh:or" in out


def test_range_on_unsupported_datatype_is_refused(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        '  sh:property [ sh:path agentce:score ; sh:minInclusive "0.5"^^xsd:decimal ] .\n',
    )
    rc, out = run([shape], capsys)
    assert rc == 1
    assert "REFUSED: minInclusive" in out


def test_range_on_integer_is_allowed(tmp_path, capsys):
    shape = write(
        tmp_path,
        "agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;\n"
        "  sh:property [ sh:path agentce:count ; sh:minInclusive 0 ] .\n",
    )
    rc, out = run([shape], capsys)
    assert rc == 0
    assert "PSP OK" in out


def test_missing_file_is_an_error(capsys):
    rc, out = run([str(HERE / "does-not-exist.ttl")], capsys)
    assert rc == 2
    assert "ERROR" in out


def test_no_arguments_is_an_error(capsys):
    rc, _ = run([], capsys)
    assert rc == 2
