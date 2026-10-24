"""Write cases.json for the one report-file rule (18.184q). Run from this folder: python make_cases.py.

Readiness, sign and report --validate read a report folder's data files by one rule in every engine
(SPEC §13.3.4 stage 4): containers nested at most 1000 deep, no NaN, Infinity or -Infinity, integers of at
most 4300 digits, no key repeated in one object (compared decoded), each JSON Lines line one object, and an
integrity status from integrity-result.schema.json's enum. Each case writes ``content`` to ``file`` in a
report folder that otherwise reads READY (``None`` deletes it); ``repeat`` replaces ``@@`` in the content
with ``open`` repeated ``times`` times and then ``close`` repeated ``times`` times."""

import json
from pathlib import Path

DESCRIPTION = (
    "One report-file rule (SPEC 13.3.4 stage 4, 18.184q): each row writes `content` to `file` in a report"
    " folder that otherwise reads READY (null content deletes it). Where `repeat` is given, `@@` in content is"
    " replaced by `open` repeated `times` times followed by `close` repeated `times` times. `readiness` is the"
    " exact reasons list readiness gives, or null where readiness refuses the input instead; `refused` is then"
    " the refusal's message (exit 3, key report.validation_failed, no readiness file: a file outside the JSON"
    " profile, SPEC 8.7, 18.60), and null otherwise. `validate` is whether report --validate over a full report with that"
    " file replaced names the file (`refused`), names nothing for it (`valid`), or is not checked (null). Read"
    " by each engine's readiness and report --validate tests (test_readiness.py and test_report.py,"
    " readiness.test.ts and reportValidate.test.ts, ReadinessTest.java and ReportValidateTest.java)."
    " Written by make_cases.py."
)
CANNOT = "cannot read report file "
PROFILE = "outside the JSON profile: {rule} at line 1 (SPEC 8.7)"
ARRAYS = {"open": "[", "close": "]"}
DIGITS = {"open": "1", "close": ""}


def case(name, file, content, refused, repeat=None, validate=True):
    """``refused`` is False (the file reads), True (readiness cannot read its shape: a NOT READY reason),
    or the JSON profile rule the file breaks at line 1 (readiness refuses the input, 18.60)."""
    row = {"id": name, "file": file, "content": content}
    if repeat is not None:
        row["repeat"] = repeat
    if isinstance(refused, str):
        row["readiness"] = None
        row["refused"] = f"{file}: {PROFILE.format(rule=refused)}"
    else:
        row["readiness"] = (
            [CANNOT + file] if refused and file != "runtime_drift.jsonl" else []
        )
        row["refused"] = None
    row["validate"] = ("refused" if refused else "valid") if validate else None
    return row


def nested(times):
    return {**ARRAYS, "times": times}


def digits(times):
    return {**DIGITS, "times": times}


DUPLICATE = "duplicate_key"
DEPTH = "depth_over_limit"
DIGIT_LIMIT = "integer_digit_limit"
NAN = "nan"
INFINITY = "infinity"

INTEGRITY = '{"stream": "s", "status": "verified", "x": @@}\n'
COVERAGE_X = '{"subjects": {}, "x": @@}'
COVERAGE_N = '{"subjects": {}, "n": @@}'
DRIFT = '[{"kind": "k", "ref": "r"}]'

CASES = [
    {
        "id": "quarantine_absent",
        "file": "quarantine.jsonl",
        "content": None,
        "readiness": ["missing report file quarantine.jsonl"],
        "refused": None,
        "validate": None,
    },
    case("integrity_status_missing", "integrity.jsonl", '{"stream": "s"}\n', True),
    case(
        "integrity_status_not_in_enum",
        "integrity.jsonl",
        '{"stream": "s", "status": "FAILED"}\n',
        True,
    ),
    case(
        "integrity_status_not_a_string",
        "integrity.jsonl",
        '{"stream": "s", "status": ["failed"]}\n',
        True,
    ),
    case("integrity_non_object_line", "integrity.jsonl", "3\n", True),
    case("applicability_non_object_line", "applicability.jsonl", "[]\n", True),
    case("quarantine_non_object_line", "quarantine.jsonl", "3\n", True),
    case(
        "runtime_drift_non_object_line",
        "runtime_drift.jsonl",
        '{"subject": "x"}\n[]\n',
        True,
    ),
    case(
        "integrity_repeated_key_blocking_first",
        "integrity.jsonl",
        '{"stream": "s", "status": "failed", "status": "verified"}\n',
        DUPLICATE,
    ),
    case(
        "integrity_repeated_key_blocking_last",
        "integrity.jsonl",
        '{"stream": "s", "status": "verified", "status": "failed"}\n',
        DUPLICATE,
    ),
    case(
        "integrity_repeated_key_escaped",
        "integrity.jsonl",
        '{"stream": "s", "st\\u0061tus": "failed", "status": "verified_weak"}\n',
        DUPLICATE,
    ),
    case(
        "applicability_repeated_key_blocking_first",
        "applicability.jsonl",
        f'{{"drift": {DRIFT}, "drift": []}}\n',
        DUPLICATE,
    ),
    case(
        "applicability_repeated_key_blocking_last",
        "applicability.jsonl",
        f'{{"drift": [], "drift": {DRIFT}}}\n',
        DUPLICATE,
    ),
    case(
        "quarantine_repeated_key",
        "quarantine.jsonl",
        '{"record": "a", "record": "b"}\n',
        DUPLICATE,
    ),
    case(
        "coverage_repeated_key_blocking_first",
        "coverage.json",
        '{"subjects": {"s": {"coverage_status": "gap"}}, "subjects": {}}',
        DUPLICATE,
    ),
    case(
        "coverage_repeated_key_blocking_last",
        "coverage.json",
        '{"subjects": {}, "subjects": {"s": {"coverage_status": "gap"}}}',
        DUPLICATE,
    ),
    case(
        "coverage_repeated_subject_escaped",
        "coverage.json",
        '{"subjects": {"s": {"coverage_status": "gap"}, "\\u0073": {"coverage_status": "ok"}}}',
        DUPLICATE,
    ),
    case(
        "assertions_repeated_key",
        "assertions.json",
        '[{"control": "OVS-03", "outcome": "insufficient_evidence", "outcome": "conformant"}]',
        DUPLICATE,
    ),
    case(
        "same_key_in_two_objects_reads",
        "coverage.json",
        '{"subjects": {"a": {"coverage_status": "ok"}, "b": {"coverage_status": "ok"}}}',
        False,
    ),
    case("coverage_nested_1000_reads", "coverage.json", COVERAGE_X, False, nested(999)),
    case(
        "coverage_nested_1001_refused", "coverage.json", COVERAGE_X, DEPTH, nested(1000)
    ),
    case(
        "coverage_nested_100000_refused",
        "coverage.json",
        COVERAGE_X,
        DEPTH,
        nested(99999),
    ),
    case(
        "integrity_line_nested_1000_reads",
        "integrity.jsonl",
        INTEGRITY,
        False,
        nested(999),
    ),
    case(
        "integrity_line_nested_1001_refused",
        "integrity.jsonl",
        INTEGRITY,
        DEPTH,
        nested(1000),
    ),
    case(
        "coverage_integer_4300_digits_reads",
        "coverage.json",
        COVERAGE_N,
        False,
        digits(4300),
    ),
    case(
        "coverage_negative_integer_4300_digits_reads",
        "coverage.json",
        '{"subjects": {}, "n": -@@}',
        False,
        digits(4300),
    ),
    case(
        "coverage_integer_4301_digits_refused",
        "coverage.json",
        COVERAGE_N,
        DIGIT_LIMIT,
        digits(4301),
    ),
    case(
        "coverage_integer_5000_digits_refused",
        "coverage.json",
        COVERAGE_N,
        DIGIT_LIMIT,
        digits(5000),
    ),
    case("coverage_nan", "coverage.json", '{"subjects": {}, "x": NaN}', NAN),
    case(
        "coverage_infinity",
        "coverage.json",
        '{"subjects": {}, "x": Infinity}',
        INFINITY,
    ),
    case(
        "integrity_negative_infinity",
        "integrity.jsonl",
        '{"stream": "s", "status": "verified", "x": -Infinity}\n',
        INFINITY,
    ),
    case("applicability_nan", "applicability.jsonl", '{"drift": [], "x": NaN}\n', NAN),
    case("quarantine_infinity", "quarantine.jsonl", '{"x": Infinity}\n', INFINITY),
    case(
        "nan_inside_a_string_reads",
        "coverage.json",
        '{"subjects": {}, "x": "NaN"}',
        False,
    ),
]

if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "cases.json"
    out.write_text(
        json.dumps({"description": DESCRIPTION, "cases": CASES}, indent=2) + "\n",
        encoding="utf-8",
    )
