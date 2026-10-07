"""records_folder_parity_check - Python and every other engine give the same answer to
`agentce assess <folder>` (SPEC §12, §13.4 AX-1; item 18.69).

Each engine's unit tests can pass while the ports disagree on a file name's sort order, a YAML
quoting rule or a refusal's timing. This check builds one tree of record folders and profiles and
runs every scenario through Python (the reference) and each engine named on the command line, each
run in its own copy of the tree, so the answers can be compared byte for byte. Each scenario runs
twice in a fresh tree: once as text (stdout and stderr together, the way a person reads it when not
on a TTY) and once with `--json`. Python runs as `agentce.cli.main` in a forked child of a process
that has compiled the evidence schema once (about 2 s a run otherwise); a sanity run holds that
runner equal to the console script.

Per scenario step it compares the exit code; with `--json` the error key, `exit_status` and the
`records` summary; as text the records lines (`read `, `not read: `, `default profile: `, the work
tree's path replaced by `$W`); and, from the out folder, its file list (less the outputs that are
engine-specific today, ENGINE_SPECIFIC), the bytes of every file in COMPARED_BYTES, coverage.json as
parsed JSON, and manifest.json's `inputs`, `run.invocation` and `limitations`. For the hostile
scenarios every engine's `not read:` lines must also equal the lines pinned in PINNED_NOT_READ.

A file name that is not valid UTF-8 is out of scope: macOS refuses such names, and on Linux Python
shows it with its surrogate escape while TypeScript shows U+FFFD (a bounded limitation named in
engines/typescript/README.md).

Run under engines/python's environment, after `pnpm build` in engines/typescript:
    uv run --project engines/python --frozen python tools/records_folder_parity_check.py typescript
Java joins with `java` once its records-folder mode lands (18.69b): ENGINES already names its command.
"""

from __future__ import annotations

import concurrent.futures
import fnmatch
import json
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import readiness_parity_check
import yaml

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "adapters" / "otel-genai" / "fixtures"
TS_ENTRY = ROOT / "engines" / "typescript" / "bin" / "agentce.js"

#: The number of scenarios this check runs; a scenario added or lost changes it on purpose.
EXPECTED_SCENARIOS = 42
#: Every engine subprocess gets this long.
STEP_TIMEOUT_S = 120
REFERENCE = "python"
RECORDS_PREFIXES = ("read ", "not read: ", "default profile: ")

#: Outputs that differ between engines today and are not claimed equal (report renderings, the
#: graph store, the skill, packaging metadata): left out of the file-list comparison.
ENGINE_SPECIFIC = (
    "graph.sqlite",
    "packaging.json",
    "skill/*",
    "claim.json",
    "results.sarif",
    "report.md",
    "report.html",
    "project.md",
    "project.html",
    "agents/*/report.md",
    "agents/*/report.html",
    "agents/*/claim.json",
    "agents/*/results.sarif",
)
#: Outputs compared byte for byte.
COMPARED_BYTES = (
    "applicability.yaml",
    "records-bundle/*",
    "assertions.json",
    "activity.json",
    "applicability.jsonl",
    "blind-spots.json",
    "integrity.jsonl",
    "quarantine.jsonl",
    "project.json",
    "oscal-ar.json",
    "agents/*/*.json",
    "packs/*/pack.json",
)


def _python_cmd() -> list[str]:
    script = Path(sys.executable).parent / "agentce"
    if not script.is_file():
        raise SystemExit(
            f"records-folder-parity: {script} is missing; run this check under engines/python's "
            "environment (uv run --project engines/python --frozen python ...)"
        )
    return [str(script)]


def _typescript_cmd() -> list[str]:
    if not (TS_ENTRY.parent.parent / "dist" / "cli.js").is_file():
        raise SystemExit(
            "records-folder-parity: engines/typescript/dist is not built (pnpm build)"
        )
    return ["node", str(TS_ENTRY)]


def _java_cmd() -> list[str]:
    return ["java", "-jar", str(readiness_parity_check.java_jar())]


#: How to start each engine's CLI. Python is the reference and always runs; the others run when named.
ENGINES: dict[str, Callable[[], list[str]]] = {
    "python": _python_cmd,
    "typescript": _typescript_cmd,
    "java": _java_cmd,
}

# ---------------------------------------------------------------------------------------------------
# The record folders
# ---------------------------------------------------------------------------------------------------

SESSION = FX / "otel-genai-agent-session" / "input.json"
FRAUD = "spiffe://corp/agents/fraud-detection-agent"
WINDOW = (
    "observation_window: {start: '2024-01-01T00:00:00Z', end: '2026-01-01T00:00:00Z'}"
)
NO_GENAI = {
    "resourceSpans": [
        {
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "a" * 32,
                            "spanId": "b" * 16,
                            "name": "GET /",
                            "startTimeUnixNano": "1700000000000000000",
                            "endTimeUnixNano": "1700000001000000000",
                            "attributes": [
                                {"key": "http.method", "value": {"stringValue": "GET"}}
                            ],
                        }
                    ]
                }
            ]
        }
    ]
}

#: Tool and model names covering each path of PyYAML's scalar analysis.
HOSTILE_NAMES = [
    *("yes", "No", "ON", "off", "y", "n", "true", "False", "null", "Null", "~"),
    *(
        "0b1010",
        "0x1F",
        "017",
        "0o17",
        "-42",
        "+1_000",
        "1_000",
        "0",
        "190:20:30",
        "1:20",
    ),
    *("3.14", "1e10", "-.inf", ".NaN", "6.8523015e+5", "685.230_15e+03", ".5", "1."),
    *("2001-12-14t21:59:43.10-05:00", "2002-12-14", "2001-12-14 21:59:43.10 -5"),
    *("<<", "=", " lead", "trail ", "a: b", "a #b", "a#b", "a:b"),
    *(
        "-x",
        "?x",
        ":x",
        ",x",
        "[x",
        "]x",
        "{x",
        "}x",
        "#x",
        "&x",
        "*x",
        "!x",
        "|x",
        ">x",
    ),
    *("'x", '"x', "%x", "@x", "`x", "- x", "? x", ": x", "-", "?"),
    *("it's", 'say "hi"', "a\tb", "a\nb", "a\rb", "a\x85b", "a b", "a\x07b", "a\x7fb"),
    *("a\xa0b", "﻿a", "é", "café", "工具", "tool😀", "aＡ", "a😀"),
    "x" * 200,
    " ".join(["word"] * 40),
    "lookup: the bureau's file # " + "with a long tail " * 6 + "é \x07",
]


def _session() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(SESSION.read_text(encoding="utf-8"))
    return doc


def _spans(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        sp
        for rs in doc["resourceSpans"]
        for ss in rs["scopeSpans"]
        for sp in ss["spans"]
    ]


def _retrace(doc: dict[str, Any], digit: str) -> dict[str, Any]:
    """``doc`` with every span on its own trace, so the duplicate rule cannot hide its events."""
    for span in _spans(doc):
        span["traceId"] = digit * 32
    return doc


def _attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _agent_doc(agent_id: str, digit: str) -> dict[str, Any]:
    doc = _retrace(_session(), digit)
    for span in _spans(doc):
        for a in span["attributes"]:
            if a["key"] == "gen_ai.agent.id":
                a["value"]["stringValue"] = agent_id
    return doc


def _names_doc() -> dict[str, Any]:
    """One trace whose tool and model names are HOSTILE_NAMES."""
    spans = []
    agent = [_attr("gen_ai.agent.id", "spiffe://corp/agents/yaml-names")]
    for i, name in enumerate(HOSTILE_NAMES):
        common = {"traceId": "7" * 32, "startTimeUnixNano": f"17342000{i:02d}000000000"}
        common["endTimeUnixNano"] = common["startTimeUnixNano"]
        spans.append(
            {
                **common,
                "spanId": f"{2 * i:016x}",
                "name": "execute_tool",
                "attributes": [
                    _attr("gen_ai.operation.name", "execute_tool"),
                    _attr("gen_ai.tool.name", name),
                    *agent,
                ],
            }
        )
        spans.append(
            {
                **common,
                "spanId": f"{2 * i + 1:016x}",
                "name": "chat",
                "attributes": [
                    _attr("gen_ai.operation.name", "chat"),
                    _attr("gen_ai.request.model", name),
                    *agent,
                ],
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr("service.name", "yaml-names")]},
                "scopeSpans": [{"scope": {"name": "probe"}, "spans": spans}],
            }
        ]
    }


def _raw_depth(payload: bytes) -> int:
    depth = deepest = 0
    in_string = escaped = False
    for byte in payload:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            deepest = max(deepest, depth)
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return deepest


def _nested(target: int, digit: str) -> bytes:
    """A session document on its own trace whose raw ``[``/``{`` nesting is exactly ``target``, the
    depth held in an attribute entry's extra key that the adapter never reads."""
    doc = _retrace(_session(), digit)
    entry: dict[str, Any] = {"key": "probe", "value": {"stringValue": "x"}, "extra": []}
    _spans(doc)[0]["attributes"].append(entry)
    deep: list[Any] = []
    while _raw_depth(raw := json.dumps(doc).encode()) < target:
        deep = [deep]
        entry["extra"] = deep
    assert _raw_depth(raw) == target, (_raw_depth(raw), target)
    return raw


def _with_value(digit: str, key: str, raw_value: str) -> bytes:
    """A session document on its own trace with ``key``'s whole value object set to ``raw_value``."""
    doc = _retrace(_session(), digit)
    _spans(doc)[1]["attributes"].append({"key": key, "value": "@@VALUE@@"})
    return json.dumps(doc).replace('"@@VALUE@@"', raw_value).encode()


def _with_attr(digit: str, key: str, raw_value: str) -> bytes:
    """A session document on its own trace with ``key`` set to ``raw_value`` (JSON string source)."""
    return _with_value(digit, key, '{"stringValue": ' + raw_value + "}")


def _replace_once(digit: str, old: str, new: str) -> bytes:
    text = json.dumps(_retrace(_session(), digit))
    assert old in text, old
    return text.replace(old, new, 1).encode()


def _line(doc: dict[str, Any] | bytes) -> bytes:
    return doc if isinstance(doc, bytes) else json.dumps(doc).encode()


def build_tree(w: Path) -> None:
    """Write every record folder and profile under ``w``; symlinks are relative, so a copy of the
    tree is self-contained."""

    def folder(name: str, files: dict[str, bytes | Path]) -> Path:
        d = w / name
        d.mkdir(parents=True)
        for rel, src in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(src.read_bytes() if isinstance(src, Path) else src)
        return d

    session = SESSION.read_bytes()
    folder("one", {"session.json": SESSION})
    folder(
        "multi",
        {
            "fraud.json": FX / "datadog" / "input.json",
            "checkout.json": FX / "langfuse" / "input.json",
            "rag.json": FX / "openinference-rag" / "input.json",
        },
    )
    folder(
        "one2",
        {"session.json": SESSION, "rag.json": FX / "openinference-rag" / "input.json"},
    )
    (w / "session-src.json").write_bytes(session)
    (w / "elsewhere").mkdir()
    (w / "elsewhere" / "x.json").write_bytes(session)
    mixed = folder(
        "mixed",
        {
            "a/session.json": session,
            "dup.json": session,
            ".hidden.json": session,
            ".git/x.json": session,
            "__pycache__/x.json": session,
            "notes.txt": b"hello",
            "old.json.gz": b"\x1f\x8b",
            "empty.json": b"",
            "junk.json": b"{not json",
            "lines.jsonl": session.replace(b"\n", b"") + b"\nnot json\n\n[]\n",
            "chat.ndjson": (FX / "otel-genai-chat" / "input.json")
            .read_bytes()
            .replace(b"\n", b"")
            + b"\n",
        },
    )
    os.symlink("../session-src.json", mixed / "outside.json")
    os.symlink("../elsewhere", mixed / "linked")
    folder("nogenai", {"http.json": json.dumps(NO_GENAI).encode()})
    folder("none", {"a.json": b"{}", "b.jsonl": b"[1]\n", "c.gz": b"x"})
    (w / "collide-out" / "records-bundle").mkdir(parents=True)
    (w / "collide-out" / "records-bundle" / "keep.txt").write_text("mine")
    folder("inside", {"session.json": SESSION, "out/keep.txt": b"mine"})
    folder("inside-empty", {"session.json": SESSION, "out/.keep": b""})
    (w / "inside-empty" / "out" / ".keep").unlink()
    (w / "link-out").mkdir()
    (w / "real-bundle").mkdir()
    (w / "real-bundle" / "manifest.json").write_text("{}")
    os.symlink("../real-bundle", w / "link-out" / "records-bundle")
    (w / "dup.yaml").write_text(
        f"profile_version: 1\n{WINDOW}\nsubjects:\n- {{id: '{FRAUD}', role: deployer}}\n"
        f"- {{id: '{FRAUD}', role: deployer}}\n"
    )
    (w / "nosub.yaml").write_text(f"profile_version: 1\n{WINDOW}\nsubjects: []\n")
    (w / "state-file").write_text("a file, so a --state under it cannot be created\n")

    # Sort order: U+FF21 sorts before U+1F600 by code point, after it by UTF-16 unit.
    folder(
        "sortorder",
        {
            "a\U0001f600.json": _line(_agent_doc("spiffe://x/a\U0001f600", "1")),
            "aＡ.json": _line(_agent_doc("spiffe://x/aＡ", "2")),
            "b.json": _line(_agent_doc("spiffe://x/b", "3")),
        },
    )
    folder("yamlnames", {"names.json": _line(_names_doc())})

    bom = b"\xef\xbb\xbf"
    folder(
        "depth",
        {
            "deep.json": _nested(257, "1"),
            "ok256.json": _nested(256, "2"),
            "nest.jsonl": _nested(256, "3") + b"\n" + _nested(257, "4") + b"\n",
            "bom256.json": bom + _nested(256, "5"),
            "bom257.json": bom + _nested(257, "6"),
            "bomline.jsonl": bom + _nested(257, "7") + b"\n",
        },
    )
    folder(
        "strings",
        {
            "brackets.json": _with_attr(
                "1", "probe.text", '"\\"' + "[" * 300 + "{" * 30 + '"'
            ),
            "pair.json": _with_attr("2", "probe.text", '"\\ud83d\\ude00"'),
            "lone-dropped.json": _with_attr("3", "probe.text", '"x\\ud800y"'),
            "lone-service.json": _replace_once(
                "4", '"credit-underwriter"}', '"cr\\ud800edit"}'
            ),
            "lone-agent.json": _replace_once(
                "5",
                '"spiffe://corp/agents/credit-underwriter"',
                '"spiffe://corp/agents/cr\\udc00edit"',
            ),
            "nan.json": b"NaN",
            "nan-field.json": json.dumps(_retrace(_session(), "6"))[:-1].encode()
            + b', "extra": NaN}',
            "nan-attr.json": _with_value("7", "probe.num", '{"doubleValue": NaN}'),
            "inf-tokens.json": _with_value(
                "8", "gen_ai.usage.input_tokens", '{"doubleValue": -Infinity}'
            ),
            "latin1.json": b'{"resourceSpans": "\xff"}',
            "empty.json": b"",
            "junk.json": b"{not json",
        },
    )
    flat = session.replace(b"\n", b"")
    folder(
        "lines",
        {
            "bad25.jsonl": b"\n".join([b"not json"] * 25 + [flat]) + b"\n",
            "crlf.jsonl": b"\r\n\r\n"
            + _line(_retrace(_session(), "2"))
            + b"\r\n   \r\nnope\r\n\r"
            + _line(_retrace(_session(), "3"))
            + b"\r",
        },
    )
    comp = folder(
        "compressed",
        {
            "ok.json": SESSION,
            "a.json.gz": b"x",
            "b.tgz": b"x",
            "c.zip": b"x",
            "d.zst": b"x",
            "e.bz2": b"x",
            "f.xz": b"x",
            "g.JSON.GZ": b"x",
            "h.JSONL": flat + b"\n",
        },
    )
    os.symlink("../session-src.json", comp / "link.json")
    newcomer = w / "newcomer"
    newcomer.mkdir()
    shutil.copytree(w / "multi", newcomer / "rec")


# ---------------------------------------------------------------------------------------------------
# The scenarios
# ---------------------------------------------------------------------------------------------------


@dataclass
class Step:
    """One `agentce assess` run: its arguments (``{W}`` is the work tree), the working directory
    relative to the tree, and the out folder its results land in, relative to the tree."""

    args: list[str]
    out: str
    cwd: str = "."


@dataclass
class Scenario:
    name: str
    steps: list[Step | Callable[[Path], None]]
    pinned_not_read: list[str] | None = None
    #: The key the last step must be refused with (exit 3, its out folder left as it was).
    refusal: str | None = None


def _assess(folder: str, *extra: str, out: str | None = None) -> Step:
    """``assess {W}/<folder> ... --out {W}/<out>`` run from the tree's root."""
    out = out or f"out-{folder.replace('/', '_')}-{len(extra)}"
    return Step(["{W}/" + folder, *extra, "--out", "{W}/" + out], out)


def _profile_edit(
    src: str, dest: str, edit: Callable[[list[Any]], list[Any]]
) -> Callable[[Path], None]:
    def run(w: Path) -> None:
        doc = yaml.safe_load((w / src).read_text(encoding="utf-8"))
        doc["subjects"] = edit(doc["subjects"])
        (w / dest).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    return run


def _r3(w: Path) -> None:
    doc = yaml.safe_load(
        (w / "r3-first/applicability.yaml").read_text(encoding="utf-8")
    )
    (named,) = doc["subjects"]
    doc["subjects"] = [named, {**named, "id": FRAUD}]
    (w / "r3.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _assess_from(cwd: str, *args: str, out: str) -> Step:
    return Step(list(args), out, cwd)


#: The `not read:` lines each hostile scenario must print, in every engine.
PINNED_NOT_READ = {
    "depth": [
        "not read: bom257.json: nested too deeply to parse safely",
        "not read: bomline.jsonl: nested too deeply to parse safely",
        "not read: deep.json: nested too deeply to parse safely",
        "not read: nest.jsonl line 2: nested too deeply to parse safely",
    ],
    "strings": [
        "not read: empty.json: invalid_json: not valid JSON",
        "not read: junk.json: invalid_json: not valid JSON",
        "not read: latin1.json: invalid_encoding: not valid UTF-8",
        "not read: lone-agent.json: invalid_encoding: a string holds a lone surrogate, which "
        "UTF-8 cannot carry",
        "not read: lone-service.json: invalid_encoding: a string holds a lone surrogate, which "
        "UTF-8 cannot carry",
        "not read: nan.json: not_otlp: top-level value is not a JSON object",
    ],
    "lines": [
        f"not read: bad25.jsonl line {n}: invalid_json: not valid JSON"
        for n in range(1, 21)
    ],
    "compressed": [
        "not read: a.json.gz: compressed files are not read; decompress them first",
        "not read: b.tgz: compressed files are not read; decompress them first",
        "not read: c.zip: compressed files are not read; decompress them first",
        "not read: d.zst: compressed files are not read; decompress them first",
        "not read: e.bz2: compressed files are not read; decompress them first",
        "not read: f.xz: compressed files are not read; decompress them first",
        "not read: g.JSON.GZ: compressed files are not read; decompress them first",
        "not read: link.json: a symlink that leaves the folder is not read",
    ],
    "sortorder": [],
}


def scenarios() -> list[Scenario]:
    collides = "input.records_out_collides"
    return [
        # The reference set (python-reference.md).
        Scenario("one", [_assess("one")]),
        Scenario("multi", [_assess("multi")]),
        Scenario("mixed", [_assess("mixed")]),
        Scenario(
            "roundtrip",
            [
                _assess("multi", out="multi-first"),
                _assess("multi", "--profile", "{W}/multi-first/applicability.yaml"),
            ],
        ),
        Scenario(
            "dup-subject",
            [_assess("multi", "--profile", "{W}/dup.yaml")],
            refusal="input.profile_invalid",
        ),
        Scenario(
            "no-subject",
            [_assess("multi", "--profile", "{W}/nosub.yaml")],
            refusal="input.profile_invalid",
        ),
        Scenario(
            "ambiguous",
            [_assess("one", "--bundle", "{W}/one")],
            refusal="input.records_source_ambiguous",
        ),
        Scenario("out-is-folder", [_assess("one", out="one")], refusal=collides),
        Scenario("out-inside", [_assess("inside", out="inside/out")], refusal=collides),
        Scenario(
            "out-not-bundle", [_assess("one", out="collide-out")], refusal=collides
        ),
        Scenario(
            "no-genai", [_assess("nogenai")], refusal="input.records_no_genai_spans"
        ),
        Scenario(
            "none-recognised",
            [_assess("none")],
            refusal="input.records_none_recognised",
        ),
        Scenario("missing", [_assess("nope")], refusal="input.records_not_a_directory"),
        Scenario(
            "package",
            [_assess("one", "--package-for-sharing")],
            refusal="input.package_requires_bundle",
        ),
        # The hostile set.
        Scenario("sortorder", [_assess("sortorder")], PINNED_NOT_READ["sortorder"]),
        Scenario(
            "sortorder-roundtrip",
            [
                _assess("sortorder", out="so-first"),
                _assess("sortorder", "--profile", "{W}/so-first/applicability.yaml"),
            ],
        ),
        Scenario("yamlnames", [_assess("yamlnames")]),
        Scenario(
            "yamlnames-roundtrip",
            [
                _assess("yamlnames", out="yn-first"),
                _assess("yamlnames", "--profile", "{W}/yn-first/applicability.yaml"),
            ],
        ),
        Scenario("depth", [_assess("depth")], PINNED_NOT_READ["depth"]),
        Scenario(
            "depth-from-inside",
            [_assess_from("depth", ".", "--out", "../depth-out", out="depth-out")],
            PINNED_NOT_READ["depth"],
        ),
        Scenario("strings", [_assess("strings")], PINNED_NOT_READ["strings"]),
        Scenario("lines", [_assess("lines")], PINNED_NOT_READ["lines"]),
        Scenario("compressed", [_assess("compressed")], PINNED_NOT_READ["compressed"]),
        Scenario("hidden-folder-arg", [_assess("mixed/.git")]),
        Scenario("pycache-folder-arg", [_assess("mixed/__pycache__")]),
        Scenario("nested-folder-arg", [_assess("mixed/a")]),
        Scenario("symlinked-folder-arg", [_assess("mixed/linked")]),
        # Every refusal.
        Scenario("out-inside-empty", [_assess("inside-empty", out="inside-empty/out")]),
        Scenario(
            "out-bundle-symlink", [_assess("one", out="link-out")], refusal=collides
        ),
        Scenario(
            "folder-is-bundle",
            [_assess("one", out="fib"), _assess("fib/records-bundle", out="fib")],
            refusal=collides,
        ),
        Scenario(
            "not-a-directory",
            [_assess("one/session.json")],
            refusal="input.records_not_a_directory",
        ),
        Scenario(
            "profile-missing",
            [_assess("one", "--profile", "{W}/no-such-profile.yaml")],
            refusal="input.profile_not_a_file",
        ),
        Scenario(
            "fail-on-invalid",
            [_assess("one", "--fail-on", "outcome ==")],
            refusal="input.fail_on_invalid_expression",
        ),
        Scenario(
            "state-unwritable",
            [_assess("one", "--state", "{W}/state-file/state")],
            refusal="input.state_dir_unwritable",
        ),
        Scenario("fail-on-records", [_assess("one", "--fail-on", 'severity=="high"')]),
        # Profiles passed back (18.77).
        Scenario(
            "roundtrip-one-agent",
            [
                _assess("one", out="one-first"),
                _assess("one", "--profile", "{W}/one-first/applicability.yaml"),
            ],
        ),
        Scenario(
            "roundtrip-reversed",
            [
                _assess("multi", out="rev-first"),
                _profile_edit(
                    "rev-first/applicability.yaml",
                    "rev.yaml",
                    lambda s: list(reversed(s)),
                ),
                _assess("multi", "--profile", "{W}/rev.yaml"),
            ],
        ),
        Scenario(
            "roundtrip-trimmed",
            [
                _assess("multi", out="trim-first"),
                _profile_edit(
                    "trim-first/applicability.yaml",
                    "trim.yaml",
                    lambda s: [x for x in s if x["id"] != FRAUD],
                ),
                _assess("multi", "--profile", "{W}/trim.yaml"),
            ],
        ),
        Scenario(
            "r3-id-less",
            [
                _assess("one2", out="r3-first"),
                _r3,
                _assess("one2", "--profile", "{W}/r3.yaml"),
            ],
        ),
        # A newcomer's forms, from one shared working directory, with the default out.
        Scenario(
            "newcomer",
            [
                _assess_from("newcomer", "./rec", out="newcomer/out"),
                _assess_from("newcomer", "rec/", out="newcomer/out"),
                _assess_from("newcomer/rec", ".", out="newcomer/rec/out"),
                _assess_from("newcomer/rec", ".", out="newcomer/rec/out"),
            ],
        ),
        Scenario("out-relative", [Step(["one", "--out", "rel-out"], "rel-out")]),
        Scenario(
            "multi-twice",
            [_assess("multi", out="twice"), _assess("multi", out="twice")],
        ),
    ]


# ---------------------------------------------------------------------------------------------------
# Running and comparing
# ---------------------------------------------------------------------------------------------------


@dataclass
class StepResult:
    exit: int
    key: str | None = None
    exit_status: Any = None
    records: Any = None
    lines: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    blobs: dict[str, bytes] = field(default_factory=dict)
    coverage: Any = None
    manifest: Any = None
    tail: str = ""
    #: Whether the out folder holds exactly what it held before the run.
    unchanged: bool = False


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CI")}
    env["NO_COLOR"] = "1"
    return env


def _matches(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatchcase(rel, p)
        and (p.count("/") == rel.count("/") or p.endswith("/*"))
        for p in patterns
    )


def _tree(out: Path) -> dict[str, bytes]:
    """Every file under ``out`` (symlinks included, not followed) and its bytes."""
    if not out.is_dir():
        return {}
    return {
        p.relative_to(out).as_posix(): (
            os.readlink(p).encode() if p.is_symlink() else p.read_bytes()
        )
        for p in sorted(out.rglob("*"))
        if p.is_symlink() or p.is_file()
    }


def _snapshot(out: Path, result: StepResult) -> None:
    if not out.is_dir():
        return
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(out).as_posix()
        if _matches(rel, ENGINE_SPECIFIC):
            continue
        result.files.append(rel)
        if _matches(rel, COMPARED_BYTES):
            result.blobs[rel] = path.read_bytes()
    if (out / "coverage.json").is_file():
        result.coverage = json.loads(
            (out / "coverage.json").read_text(encoding="utf-8")
        )
    if (out / "manifest.json").is_file():
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        result.manifest = {
            "inputs": m.get("inputs"),
            "run.invocation": (m.get("run") or {}).get("invocation"),
            "limitations": m.get("limitations"),
        }


#: Runs one `agentce assess` in a working directory; returns (exit code, stdout, stderr).
Runner = Callable[[list[str], Path], tuple[int, bytes, bytes]]


def subprocess_runner(cmd: list[str]) -> Runner:
    """An engine's CLI as a fresh process per run, with STEP_TIMEOUT_S to finish."""

    def run(args: list[str], cwd: Path) -> tuple[int, bytes, bytes]:
        try:
            proc = subprocess.run(
                [*cmd, "assess", *args],
                cwd=cwd,
                env=_env(),
                capture_output=True,
                timeout=STEP_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return -1, b"", f"TIMEOUT after {STEP_TIMEOUT_S} s".encode()
        return proc.returncode, proc.stdout, proc.stderr

    return run


def python_fork_runner(args: list[str], cwd: Path) -> tuple[int, bytes, bytes]:
    """Python's own CLI entry point (`agentce.cli.main`, what the console script calls) in a forked
    child of a process that has already compiled the evidence schema's validators, so each run is
    still a fresh process without paying that ~2 s again. stdout and stderr are files, not a TTY,
    as in a subprocess. `main` agrees with the console script: checked on every run by
    `_reference_sanity`."""
    from agentce import cli

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        pid = os.fork()
        if pid == 0:  # the child: never returns
            code = 70
            try:
                os.chdir(cwd)
                os.dup2(out.fileno(), 1)
                os.dup2(err.fileno(), 2)
                os.environ.clear()
                os.environ.update(_env())
                try:
                    code = int(cli.main(["assess", *args]) or 0)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(code)
        deadline = time.monotonic() + STEP_TIMEOUT_S
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            if time.monotonic() > deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                return -1, b"", f"TIMEOUT after {STEP_TIMEOUT_S} s".encode()
            time.sleep(0.01)
        out.seek(0)
        err.seek(0)
        return os.waitstatus_to_exitcode(status), out.read(), err.read()


def _warm_python() -> None:
    """Compile the evidence-schema validators a records run uses, once, by validating the events of
    every otel-genai fixture (validators are a pure function of the vendored schema, so a child
    forked afterwards answers exactly as a fresh process does)."""
    from agentce import schema
    from agentce.records import otel_genai

    for fixture in sorted(FX.glob("*/input.json")):
        for event in otel_genai.adapt(fixture.read_bytes(), subject="warm").events:
            schema.validate_event(event)


def run_scenario(
    run: Runner, w: Path, scenario: Scenario, as_json: bool
) -> list[StepResult]:
    """Copy the template tree (beside ``w``) to ``w`` and run ``scenario``'s steps in it."""
    shutil.copytree(w.parent.parent.parent.parent / "template", w, symlinks=True)
    results = []
    for step in scenario.steps:
        if not isinstance(step, Step):
            step(w)
            continue
        args = [a.replace("{W}", str(w)) for a in step.args] + (
            ["--json"] if as_json else []
        )
        before = _tree(w / step.out)
        code, out_bytes, err_bytes = run(args, w / step.cwd)
        stdout = out_bytes.decode("utf-8", "replace")
        stderr = err_bytes.decode("utf-8", "replace")
        result = StepResult(exit=code, tail=(stdout + stderr)[-600:])
        if as_json:
            try:
                doc = json.loads(stdout)
            except ValueError:
                doc = {"unparsed": stdout[-300:]}
            error = doc.get("error") if isinstance(doc.get("error"), dict) else {}
            result.key = error.get("key") or error.get("message_key")
            result.exit_status = doc.get("exit_status")
            result.records = doc.get("records")
        else:
            text = (stdout + stderr).replace(str(w), "$W")
            result.lines = [
                ln for ln in text.splitlines() if ln.startswith(RECORDS_PREFIXES)
            ]
        _snapshot(w / step.out, result)
        result.unchanged = _tree(w / step.out) == before
        results.append(result)
    return results


def _python_job(
    job: tuple[str, str, str],
) -> tuple[tuple[str, str, str], list[StepResult]]:
    """A pool worker's one (tree, scenario, mode) through Python's forked runner."""
    tree, name, mode = job
    scenario = next(s for s in scenarios() if s.name == name)
    return job, run_scenario(python_fork_runner, Path(tree), scenario, mode == "json")


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 300 else text[:300] + "..."


def compare(
    scenario: str, mode: str, step: int, engine: str, ref: StepResult, got: StepResult
) -> list[str]:
    where = f"{scenario} step {step} ({mode})"
    problems = []
    fields = [
        "exit",
        "key",
        "exit_status",
        "records",
        "lines",
        "files",
        "coverage",
        "manifest",
    ]
    for name in fields:
        a, b = getattr(ref, name), getattr(got, name)
        if a != b:
            problems.append(
                f"MISMATCH {where} {name}: {REFERENCE}={_short(a)} {engine}={_short(b)}"
            )
    for rel in sorted(set(ref.blobs) & set(got.blobs)):
        if ref.blobs[rel] != got.blobs[rel]:
            a_lines = ref.blobs[rel].splitlines()
            b_lines = got.blobs[rel].splitlines()
            first = next(
                (
                    i
                    for i, (x, y) in enumerate(zip(a_lines, b_lines, strict=False))
                    if x != y
                ),
                min(len(a_lines), len(b_lines)),
            )
            problems.append(
                f"MISMATCH {where} bytes of {rel} (first differing line {first + 1}): "
                f"{REFERENCE}={_short(a_lines[first] if first < len(a_lines) else b'<end>')} "
                f"{engine}={_short(b_lines[first] if first < len(b_lines) else b'<end>')}"
            )
    if problems and got.tail:
        problems.append(f"    {engine} output tail: {_short(got.tail[-300:])}")
    return problems


def _reference_sanity(tmp: Path) -> list[str]:
    """The forked runner and the console script give the same answer for scenario `one`."""
    scenario = next(s for s in scenarios() if s.name == "one")
    got = {}
    for label in ("fork", "script"):
        w = tmp / "sanity" / label / "one" / "w"
        run = (
            python_fork_runner if label == "fork" else subprocess_runner(_python_cmd())
        )
        got[label] = run_scenario(run, w, scenario, as_json=False)
    problems = []
    for a, b in zip(got["fork"], got["script"], strict=True):
        problems += compare("sanity-one", "text", 1, "python-script", a, b)
    return problems


def main(argv: list[str]) -> int:
    others = argv or ["typescript"]
    unknown = [e for e in others if e not in ENGINES or e == REFERENCE]
    if unknown:
        print(f"records-folder-parity: unknown engine(s) {unknown}", file=sys.stderr)
        return 2
    engines = [REFERENCE, *others]
    plan = scenarios()
    if len(plan) != EXPECTED_SCENARIOS:
        print(
            f"records-folder-parity: FAIL: {len(plan)} scenarios defined, expected "
            f"{EXPECTED_SCENARIOS}",
            file=sys.stderr,
        )
        return 1
    runners = {e: subprocess_runner(ENGINES[e]()) for e in others}
    workers = max(2, min(16, os.cpu_count() or 2))
    results: dict[tuple[str, str, str], list[StepResult]] = {}
    with tempfile.TemporaryDirectory(prefix="records-folder-parity-") as raw:
        tmp = Path(raw)
        build_tree(tmp / "template")
        trees = {
            (e, m, sc.name): tmp / e / m / sc.name / "w"
            for e in engines
            for m in ("text", "json")
            for sc in plan
        }
        # Python first, from a pool forked before any thread exists, then the other engines.
        _warm_python()
        python_jobs = [
            (str(w), n, m) for (e, m, n), w in trees.items() if e == REFERENCE
        ]
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            for (tree, name, mode), steps in pool.imap_unordered(
                _python_job, python_jobs
            ):
                results[(REFERENCE, mode, name)] = steps
        problems = _reference_sanity(tmp)
        by_name = {s.name: s for s in plan}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as threads:
            futures = {
                threads.submit(run_scenario, runners[e], w, by_name[n], m == "json"): (
                    e,
                    m,
                    n,
                )
                for (e, m, n), w in trees.items()
                if e != REFERENCE
            }
            for future in concurrent.futures.as_completed(futures):
                results[futures[future]] = future.result()
    for sc in plan:
        for mode in ("text", "json"):
            ref = results[(REFERENCE, mode, sc.name)]
            for engine in engines:
                got = results[(engine, mode, sc.name)]
                if engine != REFERENCE:
                    for i, (a, b) in enumerate(zip(ref, got, strict=True), 1):
                        problems += compare(sc.name, mode, i, engine, a, b)
                last = got[-1]
                if sc.refusal is not None and not (
                    last.exit == 3
                    and last.unchanged
                    and (mode == "text" or last.key == sc.refusal)
                ):
                    problems.append(
                        f"REFUSAL {sc.name} ({engine}, {mode}): expected {sc.refusal} at exit 3 "
                        f"with the out folder untouched, got exit {last.exit}, key {last.key}, "
                        f"out untouched {last.unchanged}"
                    )
                if mode == "text" and sc.pinned_not_read is not None:
                    shown = [
                        ln for r in got for ln in r.lines if ln.startswith("not read: ")
                    ]
                    if shown != sc.pinned_not_read:
                        problems.append(
                            f"PIN {sc.name} ({engine}): not-read lines {_short(shown)} differ from "
                            f"the pinned {_short(sc.pinned_not_read)}"
                        )
    for line in problems:
        print(line)
    status = "FAIL" if problems else "ok"
    print(
        f"records-folder-parity: {status}: {len(plan)} scenarios (expected {EXPECTED_SCENARIOS}), "
        f"each as text and --json, through {', '.join(engines)}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
