"""report_validate_reader_census - the input classes of the report-file reader contract (SPEC 8.7, item 18.60).

Every report file `report --validate`, `readiness`, `sign` and `diff` read goes through one reader per format per
engine: strict UTF-8 with no byte-order mark, the JSON profile for JSON and JSON Lines, RFC 4180 for report.csv,
well-formed XML 1.0 for the two XML files, and one whitespace set for the empty check of report.md and report.html.
This census lists each input class those readers decide: its id (the `rule` of a case in
spec/report/reader-cases.json), the input-class families it belongs to (encoding, line end, number lexeme, CSV
quoting, XML production, whitespace), the formats it applies to, the commands whose reader reaches it, and the
reader functions that implement it in each engine.

Modes:

* `--check-cases <file>`: the case table keeps at least one case per class and one per (class, command) the class
  reaches, so readiness, sign and diff each have their own cases; sign has a manifest.json case for utf8_bom and
  json_profile (acceptance 8); every case names a class, a command that reaches it and a format it applies to; ids
  are unique and sorted; a refusal (exit 3) carries `key` and `names`; a readiness or sign case is a refusal. One
  line per problem, exit 1 on any.
* `--check-code`: each engine's reader module (agentce/report_reader.py, src/reportReader.ts, ReportReader.java)
  has no public function the census does not name, and every function the census names is defined in its file.
* `--self-test` (no build, no engine source read): drops one class's cases, adds a case of an unlisted class, and
  seeds the other case faults, each of which must be caught; then runs the code check over synthetic source text
  with an unnamed public function added and a named one removed, in each language, and expects both caught.
* no argument: `--check-cases spec/report/reader-cases.json` and `--check-code`.

Standard library only, so verification/run's registry lint can load it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "spec" / "report" / "reader-cases.json"

ENGINES = ("python", "typescript", "java")
FAMILIES = (
    "encoding",
    "line end",
    "number lexeme",
    "CSV quoting",
    "XML production",
    "whitespace",
)
COMMANDS = ("report-validate", "readiness", "sign", "diff")
OPS = ("write", "prepend", "append", "replace", "replace_all")
#: The format of a report file, by suffix.
FORMATS = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".sarif": "sarif",
    ".csv": "csv",
    ".xml": "xml",
    ".md": "md",
    ".html": "html",
}
#: (file, rule) pairs sign's own readers must keep a case for (acceptance 8): sign parses manifest.json itself.
SIGN_REQUIRED = (("manifest.json", "utf8_bom"), ("manifest.json", "json_profile"))
#: Commands whose every case is a refusal (acceptance 7 and 8); their happy paths are run outside the table.
REFUSAL_ONLY = ("readiness", "sign")

PY_READER = "engines/python/agentce/report_reader.py"
PY_JSON = "engines/python/agentce/safe_json.py"
PY_REPORT = "engines/python/agentce/report.py"
TS_READER = "engines/typescript/src/reportReader.ts"
TS_JSON = "engines/typescript/src/json.ts"
TS_REPORT = "engines/typescript/src/reportValidate.ts"
JAVA_READER = "engines/java/src/main/java/org/agentce/ReportReader.java"
JAVA_JSON = "engines/java/src/main/java/org/agentce/Json.java"
JAVA_REPORT = "engines/java/src/main/java/org/agentce/ReportValidate.java"
#: The module per engine whose every public function the census must name.
READER_MODULES = {"python": PY_READER, "typescript": TS_READER, "java": JAVA_READER}

Readers = Mapping[str, tuple[tuple[str, str], ...]]


def _readers(python: list[str], typescript: list[str], java: list[str]) -> Readers:
    """`file:function` strings per engine as (file, function) pairs."""

    def pairs(items: list[str]) -> tuple[tuple[str, str], ...]:
        return tuple(
            (path, name) for path, name in (item.rsplit(":", 1) for item in items)
        )

    return {
        "python": pairs(python),
        "typescript": pairs(typescript),
        "java": pairs(java),
    }


TEXT_DECODE = _readers(
    [f"{PY_READER}:decode_report_text"],
    [f"{TS_READER}:decodeReportText"],
    [f"{JAVA_READER}:decodeReportText"],
)
JSON_DECODE = _readers(
    [f"{PY_JSON}:read_json", f"{PY_JSON}:read_jsonl"],
    [f"{TS_JSON}:decodeProfile", f"{TS_JSON}:readJson", f"{TS_JSON}:readJsonl"],
    [f"{JAVA_JSON}:decodeProfile", f"{JAVA_JSON}:readJson"],
)
JSON_PROFILE = _readers(
    [f"{PY_JSON}:read_json", f"{PY_JSON}:read_jsonl", f"{PY_JSON}:check_profile"],
    [f"{TS_JSON}:readJson", f"{TS_JSON}:readJsonl", f"{TS_JSON}:checkProfile"],
    [
        f"{JAVA_JSON}:readJson",
        f"{JAVA_JSON}:readJsonlLine",
        f"{JAVA_JSON}:checkProfile",
    ],
)
SARIF_INTEGER = _readers(
    [f"{PY_REPORT}:validate_sarif_2_1_0"],
    [f"{TS_REPORT}:validateSarif210"],
    [f"{JAVA_REPORT}:validateSarif210"],
)
CSV = _readers(
    [f"{PY_READER}:read_csv"], [f"{TS_READER}:readCsv"], [f"{JAVA_READER}:readCsv"]
)
XML = _readers(
    [f"{PY_READER}:check_xml"], [f"{TS_READER}:checkXml"], [f"{JAVA_READER}:checkXml"]
)
BLANK = _readers(
    [f"{PY_READER}:is_blank"], [f"{TS_READER}:isBlank"], [f"{JAVA_READER}:isBlank"]
)


def _join(*groups: Readers) -> Readers:
    return {
        engine: tuple(pair for group in groups for pair in group[engine])
        for engine in ENGINES
    }


@dataclass(frozen=True)
class ReaderClass:
    id: str
    families: tuple[str, ...]
    formats: tuple[str, ...]
    commands: tuple[str, ...]
    readers: Readers


TEXT_FORMATS = ("csv", "xml", "md", "html")
JSON_FORMATS = ("json", "jsonl", "sarif")
VALIDATE = ("report-validate",)


def _csv(cid: str, *families: str) -> ReaderClass:
    return ReaderClass(cid, families, ("csv",), VALIDATE, CSV)


def _xml(cid: str, *families: str) -> ReaderClass:
    return ReaderClass(cid, families, ("xml",), VALIDATE, XML)


CENSUS: tuple[ReaderClass, ...] = (
    ReaderClass(
        "utf8_bom",
        ("encoding",),
        JSON_FORMATS + TEXT_FORMATS,
        ("report-validate", "readiness", "sign", "diff"),
        _join(TEXT_DECODE, JSON_DECODE),
    ),
    ReaderClass(
        "invalid_utf8",
        ("encoding",),
        JSON_FORMATS + TEXT_FORMATS,
        ("report-validate", "readiness", "sign"),
        _join(TEXT_DECODE, JSON_DECODE),
    ),
    ReaderClass(
        "json_profile",
        ("number lexeme", "line end"),
        JSON_FORMATS,
        ("report-validate", "readiness", "sign", "diff"),
        JSON_PROFILE,
    ),
    ReaderClass(
        "sarif_integer", ("number lexeme",), ("sarif",), VALIDATE, SARIF_INTEGER
    ),
    _csv("csv_bare_cr", "line end"),
    _csv("csv_text_after_quote", "CSV quoting"),
    _csv("csv_quote_in_field", "CSV quoting"),
    _csv("csv_unclosed_quote", "CSV quoting"),
    _csv("csv_quoted_line_end", "CSV quoting", "line end"),
    _csv("csv_field_count", "CSV quoting"),
    _csv("csv_trailing_empty_line", "line end"),
    _xml("xml_dtd", "XML production"),
    _xml("xml_entity", "XML production"),
    _xml("xml_after_root", "XML production"),
    _xml("xml_control_char", "XML production", "encoding"),
    _xml("xml_unbound_prefix", "XML production"),
    _xml("xml_syntax", "XML production"),
    ReaderClass("whitespace_set", ("whitespace",), ("md", "html"), VALIDATE, BLANK),
)


def census_problems(census: tuple[ReaderClass, ...]) -> list[str]:
    """The census's own shape: known families, formats, commands and engines, unique ids."""
    problems: list[str] = []
    seen: set[str] = set()
    for cls in census:
        if cls.id in seen:
            problems.append(f"census: class {cls.id} is listed twice")
        seen.add(cls.id)
        for family in cls.families:
            if family not in FAMILIES:
                problems.append(f"census: class {cls.id}: unknown family {family!r}")
        for fmt in cls.formats:
            if fmt not in FORMATS.values():
                problems.append(f"census: class {cls.id}: unknown format {fmt!r}")
        for command in cls.commands:
            if command not in COMMANDS:
                problems.append(f"census: class {cls.id}: unknown command {command!r}")
        for engine in ENGINES:
            if not cls.readers.get(engine):
                problems.append(f"census: class {cls.id}: no {engine} reader named")
    return problems


def _is_hex(value: Any) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{2})*", value) is not None
    )


def _case_problems(
    case: Any, index: int, by_id: Mapping[str, ReaderClass]
) -> list[str]:
    if not isinstance(case, dict):
        return [f"case #{index}: not an object"]
    cid = case.get("id")
    where = f"case {cid}" if isinstance(cid, str) and cid else f"case #{index}"
    problems: list[str] = []
    if not (isinstance(cid, str) and cid):
        problems.append(f"{where}: no id")
    rule, command, file = case.get("rule"), case.get("command"), case.get("file")
    cls = by_id.get(rule) if isinstance(rule, str) else None
    if cls is None:
        problems.append(f"{where}: rule {rule!r} is not a census class")
    if command not in COMMANDS:
        problems.append(f"{where}: unknown command {command!r}")
    elif cls is not None and command not in cls.commands:
        problems.append(f"{where}: {command} does not reach class {cls.id}")
    fmt = FORMATS.get(Path(file).suffix) if isinstance(file, str) and file else None
    if fmt is None:
        problems.append(f"{where}: file {file!r} is not a report file format")
    elif cls is not None and fmt not in cls.formats:
        problems.append(f"{where}: class {cls.id} does not apply to {fmt} ({file})")
    code = case.get("exit")
    if code not in (0, 3) or isinstance(code, bool):
        problems.append(f"{where}: exit {code!r} is not 0 or 3")
    elif code == 3:
        for field in ("key", "names"):
            if not (isinstance(case.get(field), str) and case[field]):
                problems.append(f"{where}: exit 3 without {field}")
    else:
        for field in ("key", "names"):
            if case.get(field) is not None:
                problems.append(f"{where}: exit 0 with a {field}")
    if command in REFUSAL_ONLY and code != 3:
        problems.append(f"{where}: a {command} case must be a refusal (exit 3)")
    edits = case.get("edits")
    if not isinstance(edits, list):
        problems.append(f"{where}: edits is not a list")
        edits = []
    for n, edit in enumerate(edits):
        if not isinstance(edit, dict) or edit.get("op") not in OPS:
            problems.append(f"{where}: edit {n} has no known op")
            continue
        if not _is_hex(edit.get("hex")):
            problems.append(f"{where}: edit {n} hex is not lower-case hex bytes")
        if edit["op"] in ("replace", "replace_all") and not (
            _is_hex(edit.get("find_hex")) and edit["find_hex"]
        ):
            problems.append(f"{where}: edit {n} {edit['op']} without find_hex")
    return problems


def check_cases(table: Any, census: tuple[ReaderClass, ...] = CENSUS) -> list[str]:
    """One line per problem with the case table `table` (the parsed reader-cases.json)."""
    if not (
        isinstance(table, dict)
        and table.get("version") == 1
        and isinstance(table.get("cases"), list)
    ):
        return ["reader-cases: expected version 1 and a cases list"]
    cases: list[Any] = table["cases"]
    by_id = {cls.id: cls for cls in census}
    problems = census_problems(census)
    ids: list[str] = []
    for index, case in enumerate(cases):
        problems.extend(_case_problems(case, index, by_id))
        if isinstance(case, dict) and isinstance(case.get("id"), str):
            ids.append(case["id"])
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    problems.extend(f"case {i}: id is not unique" for i in duplicates)
    if ids != sorted(ids):
        first = next(a for a, b in zip(ids, sorted(ids)) if a != b)
        problems.append(f"cases are not sorted by id (first out of place: {first})")
    have = {
        (case.get("rule"), case.get("command"))
        for case in cases
        if isinstance(case, dict)
    }
    have_files = {
        (case.get("file"), case.get("rule"), case.get("command"))
        for case in cases
        if isinstance(case, dict)
    }
    for cls in census:
        if not any(rule == cls.id for rule, _ in have):
            problems.append(f"class {cls.id}: no case")
            continue
        for command in cls.commands:
            if (cls.id, command) not in have:
                problems.append(f"class {cls.id}: no {command} case")
    for file, rule in SIGN_REQUIRED:
        if (file, rule, "sign") not in have_files:
            problems.append(f"sign: no {file} case for class {rule}")
    return problems


# --- the code check -------------------------------------------------------------------------------

_PUBLIC = {
    "python": re.compile(r"^(?:async\s+)?def\s+([A-Za-z]\w*)\s*\(", re.MULTILINE),
    "typescript": re.compile(
        r"^export\s+(?:async\s+)?function\s*\*?\s*(\w+)\s*[<(]", re.MULTILINE
    ),
    "java": re.compile(
        r"^\s*public\s+static\s+(?!(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\b)"
        r"(?:final\s+|synchronized\s+)*(?:<[^>]*>\s+)?[\w<>\[\],.? ]+?\s+(\w+)\s*\(",
        re.MULTILINE,
    ),
}


def _defines(language: str, source: str, name: str) -> bool:
    n = re.escape(name)
    pattern = {
        "python": rf"^(?:async\s+)?def\s+{n}\s*\(",
        "typescript": rf"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*{n}\s*[<(]",
        "java": rf"^\s*(?:(?:public|private|protected|static|final|synchronized)\s+)+"
        rf"(?:<[^>]*>\s+)?[\w<>\[\],.? ]+?\s+{n}\s*\(",
    }[language]
    return re.search(pattern, source, re.MULTILINE) is not None


def public_functions(language: str, source: str) -> list[str]:
    """The public functions of a reader module: Python top-level defs not starting with `_`, TypeScript
    `export function`s, Java `public static` methods (nested classes excluded)."""
    return sorted(set(_PUBLIC[language].findall(source)))


def check_code(
    read: Callable[[str], str | None], census: tuple[ReaderClass, ...] = CENSUS
) -> list[str]:
    """`read(path)` gives a repository file's text, or None when it does not exist."""
    problems: list[str] = []
    sources: dict[str, str | None] = {}

    def source(path: str) -> str | None:
        if path not in sources:
            sources[path] = read(path)
        return sources[path]

    named: dict[str, set[str]] = {}
    for cls in census:
        for engine in ENGINES:
            for path, name in cls.readers[engine]:
                named.setdefault(path, set()).add(name)
    language_of = {
        path: engine
        for cls in census
        for engine in ENGINES
        for path, _ in cls.readers[engine]
    }
    for path in sorted(named):
        text = source(path)
        if text is None:
            problems.append(f"code: {path} does not exist")
            continue
        for name in sorted(named[path]):
            if not _defines(language_of[path], text, name):
                problems.append(f"code: {path} defines no {name} (named by the census)")
    for engine, path in READER_MODULES.items():
        text = source(path)
        if text is None:
            continue  # reported above
        for name in public_functions(engine, text):
            if name not in named.get(path, set()):
                problems.append(
                    f"code: {path} public function {name} is in no census class"
                )
    return problems


def _read_repo(path: str) -> str | None:
    target = ROOT / path
    return target.read_text(encoding="utf-8") if target.is_file() else None


# --- self-test ------------------------------------------------------------------------------------


def _synthetic_table(census: tuple[ReaderClass, ...]) -> dict[str, Any]:
    """One accepted case per (class, command) the census reaches, plus sign's manifest.json pairs."""
    cases: list[dict[str, Any]] = []
    for cls in census:
        fmt = cls.formats[0]
        suffix = next(s for s, f in FORMATS.items() if f == fmt)
        for command in cls.commands:
            refuse = command in REFUSAL_ONLY
            file = (
                "manifest.json"
                if command == "sign" and fmt == "json"
                else f"file{suffix}"
            )
            cases.append(
                {
                    "id": f"{cls.id}-{command}",
                    "rule": cls.id,
                    "command": command,
                    "file": file,
                    "edits": [{"op": "prepend", "hex": "efbbbf"}],
                    "exit": 3 if refuse else 0,
                    "key": "k" if refuse else None,
                    "names": cls.id if refuse else None,
                }
            )
    cases.sort(key=lambda c: c["id"])
    return {"version": 1, "cases": cases}


def _synthetic_sources(census: tuple[ReaderClass, ...]) -> dict[str, str]:
    """Source text defining exactly the census-named functions, in each file's own language."""
    texts: dict[str, list[str]] = {}
    for cls in census:
        for engine in ENGINES:
            for path, name in cls.readers[engine]:
                line = {
                    "python": f"def {name}(data: bytes) -> str:\n    return ''\n",
                    "typescript": f"export function {name}(data: Buffer): string {{\n  return '';\n}}\n",
                    "java": f'    public static String {name}(byte[] data) {{\n        return "";\n    }}\n',
                }[engine]
                if line not in texts.setdefault(path, []):
                    texts[path].append(line)
    sources = {path: "".join(lines) for path, lines in texts.items()}
    # shapes that are not public functions and must not count as such
    sources[PY_READER] += (
        "class ReaderError(ValueError):\n    def rule(self) -> str:\n        return ''\n"
        "def _private() -> None:\n    pass\n"
    )
    sources[TS_READER] += (
        "export class ReaderError extends Error {}\nfunction helper(): void {}\n"
    )
    sources[JAVA_READER] = (
        "public final class ReportReader {\n"
        "    public static final class ReaderException extends IllegalArgumentException {\n"
        "        public ReaderException(String m) { super(m); }\n    }\n"
        "    private static boolean helper(String s) { return true; }\n"
        + sources[JAVA_READER]
        + "}\n"
    )
    return sources


def self_test() -> int:
    failures: list[str] = []

    def expect(name: str, problems: list[str], needle: str | None) -> None:
        if needle is None:
            if problems:
                failures.append(f"{name}: a clean input was refused: {problems}")
        elif not any(needle in p for p in problems):
            failures.append(
                f"{name}: expected a problem naming {needle!r}, got {problems}"
            )

    if census_problems(CENSUS):
        failures.append(f"the census itself is malformed: {census_problems(CENSUS)}")

    good = _synthetic_table(CENSUS)
    expect("clean table", check_cases(good), None)

    def variant(
        edit: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        cases = [dict(c) for c in good["cases"]]
        return {"version": 1, "cases": edit(cases)}

    expect(
        "a class with its cases dropped",
        check_cases(variant(lambda cs: [c for c in cs if c["rule"] != "csv_bare_cr"])),
        "class csv_bare_cr: no case",
    )

    def unlisted(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        extra = {**cs[0], "id": "zzz-unlisted", "rule": "csv_unlisted_class"}
        return [*cs, extra]

    expect(
        "a case of an unlisted class",
        check_cases(variant(unlisted)),
        "rule 'csv_unlisted_class' is not a census class",
    )
    expect(
        "a readiness-reachable class with no readiness case",
        check_cases(
            variant(lambda cs: [c for c in cs if c["id"] != "invalid_utf8-readiness"])
        ),
        "class invalid_utf8: no readiness case",
    )
    expect(
        "no sign manifest.json case for utf8_bom",
        check_cases(variant(lambda cs: [c for c in cs if c["id"] != "utf8_bom-sign"])),
        "sign: no manifest.json case for class utf8_bom",
    )
    expect(
        "a duplicate id",
        check_cases(variant(lambda cs: sorted([*cs, cs[0]], key=lambda c: c["id"]))),
        "id is not unique",
    )
    expect(
        "unsorted ids",
        check_cases(variant(lambda cs: list(reversed(cs)))),
        "not sorted by id",
    )

    def no_names(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        target = next(c for c in cs if c["exit"] == 3)
        target.pop("names")
        return cs

    expect(
        "exit 3 without names", check_cases(variant(no_names)), "exit 3 without names"
    )

    def no_key(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        next(c for c in cs if c["exit"] == 3)["key"] = None
        return cs

    expect("exit 3 without key", check_cases(variant(no_key)), "exit 3 without key")

    def readiness_accept(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        target = next(c for c in cs if c["command"] == "readiness")
        target.update(exit=0, key=None, names=None)
        return cs

    expect(
        "a readiness case that is not a refusal",
        check_cases(variant(readiness_accept)),
        "must be a refusal",
    )

    def wrong_command(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        next(c for c in cs if c["rule"] == "xml_dtd")["command"] = "readiness"
        return cs

    expect(
        "a case whose command does not reach its class",
        check_cases(variant(wrong_command)),
        "readiness does not reach class xml_dtd",
    )

    # the code check, over synthetic source text (the real modules are not read here)
    sources = _synthetic_sources(CENSUS)
    expect("clean sources", check_code(sources.get), None)
    seeded = {
        "python": (
            PY_READER,
            "def read_lines(text: str) -> list[str]:\n    return []\n",
        ),
        "typescript": (
            TS_READER,
            "export function readLines(text: string): string[] {\n  return [];\n}\n",
        ),
        "java": (
            JAVA_READER,
            "    public static List<String> readLines(String text) {\n        return null;\n    }\n",
        ),
    }
    for engine, (path, extra) in seeded.items():
        added = dict(sources)
        added[path] = (
            added[path][: added[path].rindex("}")] + extra + "}\n"
            if engine == "java"
            else added[path] + extra
        )
        name = "read_lines" if engine == "python" else "readLines"
        expect(
            f"{engine}: an unnamed public reader function",
            check_code(added.get),
            f"public function {name} is in no census class",
        )
    removed = {
        "python": (PY_READER, "read_csv"),
        "typescript": (TS_READER, "checkXml"),
        "java": (JAVA_READER, "isBlank"),
    }
    for engine, (path, name) in removed.items():
        gone = dict(sources)
        gone[path] = re.sub(rf"\b{name}\b", f"{name}Renamed", gone[path])
        expect(
            f"{engine}: a census-named function missing from its file",
            check_code(gone.get),
            f"defines no {name}",
        )
    missing = {k: v for k, v in sources.items() if k != TS_READER}
    expect(
        "a reader module that does not exist",
        check_code(missing.get),
        f"{TS_READER} does not exist",
    )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "report_validate_reader_census self-test: a class with no case, an unlisted class, the other case "
        "faults and an unnamed or missing reader function in each engine are all caught"
    )
    return 0


def _report(label: str, problems: list[str]) -> int:
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{label}: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"{label}: OK ({len(CENSUS)} classes)")
    return 0


def run_check_cases(path: Path) -> int:
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _report("reader-cases", [f"reader-cases: cannot read {path}: {exc}"])
    count = (
        len(table["cases"])
        if isinstance(table, dict) and isinstance(table.get("cases"), list)
        else 0
    )
    return _report(f"reader-cases {count} cases", check_cases(table))


def run_check_code() -> int:
    return _report("reader-code", check_code(_read_repo))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_validate_reader_census", description=__doc__.split("\n")[0]
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--check-cases", type=Path, metavar="FILE")
    mode.add_argument("--check-code", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.check_cases is not None:
        return run_check_cases(args.check_cases)
    if args.check_code:
        return run_check_code()
    cases_code = run_check_cases(CASES)
    return cases_code | run_check_code()


if __name__ == "__main__":
    raise SystemExit(main())
