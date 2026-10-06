"""verify_flow_census - every point where `agentce verify --catalog/--release` reads untrusted input,
and the mutations C3 generates from that list (item 18.28).

Two verifier rounds each found cross-engine divergences that the hand-written C3 scenarios could not
see, because each fix covered only the vectors a probe had happened to build. This module replaces
"the vectors someone thought of" with a declared census of flow points (the documents and fields the
three engines read while verifying a catalog or a release) and generates C3's mutations from the real
signed fixtures by walking them: every JSON node gets every other JSON type and a deletion, every
JSON file gets every byte-level problem class, and every artifact name gets every path class.
`verify_parity_check.py` runs each generated mutation through all three engines and requires
byte-identical output with no `internal.unexpected`, so a later change cannot reopen a class
silently.

Mutations that need a fresh signature (a field inside the signed statement, or inside a certificate
the authority signs) are produced by `build_signed_variants`, which the fixture builder calls inside
`engines/python`'s own environment and passes the signing modules in: this module itself imports no
cryptography.

Usage: `uv run --project tools python tools/verify_flow_census.py` prints the census and the number
of mutations per flow point; `--self-test` checks the walker and that every flow point generates at
least one mutation.
"""

from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar, cast

CATALOG_SIGNATURE_NAME = "catalog.sig.json"


#: The engines `verify_parity_check` runs a flow point's mutations through. `--report` is not
#: ported to TypeScript or Java (both refuse it before opening anything, `target-argument`'s
#: `report-empty` row pins that), so its rows run Python alone and require a clean refusal.
ALL_ENGINES = ("python", "typescript", "java")
PYTHON_ONLY = ("python",)


@dataclass(frozen=True)
class FlowPoint:
    id: str
    reads: str
    problem_classes: tuple[str, ...]
    engines: tuple[str, ...] = ALL_ENGINES


#: The census. Each row is one place an engine reads untrusted input during `verify --catalog` or
#: `verify --release`; its problem classes are the ways that input can be wrong. Every mutation below
#: names exactly one row, and the self-test fails if a row generates none.
FLOW_POINTS = (
    FlowPoint(
        "catalog-sig-file",
        "catalog.sig.json bytes -> JSON (verify_catalog_directory)",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "unreadable",
        ),
    ),
    FlowPoint(
        "catalog-envelope",
        "catalog.sig.json envelope fields: payloadType, payload, signatures[], keyid, sig",
        ("json-type", "missing-field", "base64"),
    ),
    FlowPoint(
        "catalog-statement",
        "the signed catalog statement: payload bytes -> JSON -> subject[0].digest.sha256",
        ("json-type", "missing-field", "payload-bytes"),
    ),
    FlowPoint(
        "catalog-tree",
        "the catalog directory's files -> digest_tree (names, order, links, content)",
        ("content", "file-name-order", "link", "empty-directory", "unreadable"),
    ),
    FlowPoint(
        "release-file",
        "a single-file --release envelope's bytes -> JSON",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "unreadable",
        ),
    ),
    FlowPoint(
        "release-envelope",
        "a single-file --release envelope's fields, kms and certificate entries",
        ("json-type", "missing-field", "base64"),
    ),
    FlowPoint(
        "certificate",
        "a keyless certificate's fields after the authority's signature verifies "
        "(issuer, identity, public_key, algorithm, validity)",
        ("json-type", "missing-field", "base64", "certificate-field"),
    ),
    FlowPoint(
        "manifest-file",
        "release-manifest.json bytes -> JSON -> canonical form (manifest digest)",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "unreadable",
        ),
    ),
    FlowPoint(
        "manifest-fields",
        "release-manifest.json's artifacts[] entries: name, digest",
        ("json-type", "missing-field"),
    ),
    FlowPoint(
        "artifact-path",
        "an artifact name -> a file inside the release directory",
        ("path", "content", "unreadable"),
    ),
    FlowPoint(
        "signatures-file",
        "signatures.json bytes -> JSON",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "unreadable",
        ),
    ),
    FlowPoint(
        "signature-entries",
        "signatures.json entries: profile, target, envelope and the envelope's own fields",
        ("json-type", "missing-field", "base64"),
    ),
    FlowPoint(
        "release-statement",
        "a bundle signature's signed statement: payload bytes -> JSON -> subject[0].digest.sha256",
        ("json-type", "missing-field", "payload-bytes"),
    ),
    FlowPoint(
        "bundle-manifest",
        "a --bundle directory's own manifest.json bytes -> JSON (load_bundle)",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "json-type",
            "missing-field",
            "source-id",
            "unreadable",
        ),
    ),
    FlowPoint(
        "bundle-files",
        "manifest.json's files[] entries: each entry's path resolved inside the bundle "
        "(confine_to_root) and its content hashed",
        ("path", "content", "unreadable"),
    ),
    FlowPoint(
        "bundle-streams",
        "each events/*.jsonl file manifest.json names: bytes -> lines -> one JSON event per line "
        "(ingest), with the manifest's digest updated so the line itself is what is read",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "line-break",
            "whitespace",
            "json-type",
            "unreadable",
        ),
    ),
    FlowPoint(
        "target-argument",
        "verify's own --catalog/--release/--bundle/--report value: empty, trailing-slash, '.', "
        "the flag given twice, and the --flag=value spelling",
        ("empty", "trailing-slash", "dot", "repeated", "equals", "unreadable"),
    ),
    FlowPoint(
        "verify-argv",
        "verify's argv grammar beyond the target values: a flag missing its value, a value on a "
        "flag that takes none, unknown and abbreviated flags, positionals, '--', and the = form of "
        "--signer-trust-root/--expect-keyid",
        (
            "missing-value",
            "takes-no-value",
            "unknown-flag",
            "abbreviation",
            "positional",
            "double-dash",
            "equals",
        ),
    ),
    FlowPoint(
        "report-claim",
        "--report's claim.json bytes -> JSON -> signatures[] and the canonical claim body, read "
        "before any signature check (plain tampering, no re-signing)",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "json-type",
            "missing-field",
            "base64",
            "unreadable",
        ),
        PYTHON_ONLY,
    ),
    FlowPoint(
        "report-trust-root",
        "--report's embedded trust-root.json bytes -> JSON -> TrustRoot (load_trust_root)",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "json-type",
            "missing-field",
            "base64",
            "unreadable",
        ),
        PYTHON_ONLY,
    ),
    FlowPoint(
        "signer-trust-root",
        "--signer-trust-root's file, the same loader as report-trust-root reached through argv: "
        "bytes -> JSON -> TrustRoot, and the argv value's own spellings",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "json-type",
            "missing-field",
            "base64",
            "empty",
            "trailing-slash",
            "unreadable",
        ),
        PYTHON_ONLY,
    ),
    FlowPoint(
        "report-statement",
        "the claimant statement a verified signature carries (predicate, subject[].name/digest), "
        "re-signed with the report's own embedded key: anyone can write trust-root.json and sign",
        ("json-type", "missing-field"),
        PYTHON_ONLY,
    ),
    FlowPoint(
        "report-manifest",
        "--report's manifest.json bytes -> JSON -> engine, outputs (names opened inside the "
        "report), inputs, limitations; re-signed so the digest gate passes",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "json-type",
            "missing-field",
            "unreadable",
        ),
        PYTHON_ONLY,
    ),
    FlowPoint(
        "report-packaging",
        "--report's packaging.json bytes -> JSON -> packaged, catalog_dir_order, "
        "catalog_dir_digests; re-signed through the manifest's outputs digest",
        (
            "bytes",
            "number-token",
            "nesting",
            "duplicate-key",
            "not-a-file",
            "json-type",
            "missing-field",
            "unreadable",
        ),
        PYTHON_ONLY,
    ),
)
ENGINES_BY_FLOW = {point.id: point.engines for point in FLOW_POINTS}

#: The flow points with no file or folder of their own to make unreadable (chmod 000), and why. The
#: self-test fails if a filesystem read site in the surface inventory names one of them (18.68).
UNREADABLE_EXEMPT = {
    "catalog-envelope": "the fields of catalog.sig.json, read by catalog-sig-file",
    "catalog-statement": "the signed payload inside catalog.sig.json, read by catalog-sig-file",
    "certificate": "a certificate inside a release envelope, read by release-file or signatures-file",
    "manifest-fields": "the entries of release-manifest.json, read by manifest-file",
    "release-envelope": "the fields of a single-file release envelope, read by release-file",
    "release-statement": "the signed payload inside signatures.json, read by signatures-file",
    "report-statement": "the signed payload inside claim.json, read by report-claim",
    "signature-entries": "the entries of signatures.json, read by signatures-file",
    "verify-argv": "verify's argv tokens; the files they name are target-argument's",
}

#: The key each unreadable row must reach in Python, the reference, so a row that refuses for another
#: reason fails the census rather than passing on agreement alone (18.68). Not asserted as root, where
#: a mode-000 file still reads.
UNREADABLE_EXPECTED = {
    "catalog-sig-file:unreadable": "input.catalog_unreadable",
    "catalog-tree:unreadable-file": "input.catalog_unreadable",
    "catalog-tree:unreadable-subdirectory": "input.catalog_unreadable",
    "release-file:release-kms:unreadable": "input.release_unreadable",
    "release-file:release-cert:unreadable": "input.release_unreadable",
    "manifest-file:unreadable": "input.release_unreadable",
    "artifact-path:artifact-unreadable": "input.release_unreadable",
    "signatures-file:unreadable": "input.release_unreadable",
    "bundle-manifest:unreadable": "input.bundle_unreadable",
    "bundle-files:unreadable-file": "input.bundle_unreadable",
    "bundle-files:unreadable-events-directory": "input.bundle_unreadable",
    "bundle-streams:unreadable-stream": "input.bundle_unreadable",
    "target-argument:catalog-unreadable": "input.catalog_unreadable",
    "target-argument:release-unreadable": "input.release_unreadable",
    "target-argument:bundle-unreadable": "input.bundle_unreadable",
    "report-claim:unreadable": "verify.report_unreadable",
    "report-claim:report-directory-unreadable": "verify.report_unreadable",
    "report-trust-root:unreadable": "input.trust_root_invalid",
    "signer-trust-root:unreadable": "input.trust_root_invalid",
    "report-manifest:unreadable": "verify.report_unreadable",
    "report-manifest:unreadable-output": "verify.report_unreadable",
    "report-manifest:unreadable-output-directory": "verify.report_unreadable",
    "report-packaging:unreadable": "verify.report_unreadable",
    "report-packaging:unreadable-profile": "verify.report_unreadable",
    "report-packaging:unreadable-domain": "verify.report_unreadable",
    "report-packaging:unreadable-evidence-directory": "verify.report_unreadable",
}
FLOW_IDS = frozenset(point.id for point in FLOW_POINTS)

#: The real stream file `bundle-streams` mutates (`corpus/quickstart/evidence`).
STREAM_FILE = "events/urn-agentce-source-langgraph-gateway-eu-1.jsonl"

#: Characters some languages' own trim removes and JSON does not allow around a value: each must
#: make a stream line unreadable in all three engines alike (18.65 round 3).
WHITESPACE_PREFIXES = (
    ("prefix-nbsp", "\u00a0".encode()),
    ("prefix-bom-mid-file", "\ufeff".encode()),
    ("prefix-em-space", "\u2003".encode()),
    ("prefix-unit-separator", b"\x1f"),
    ("prefix-tab", b"\t"),
    ("prefix-space", b" "),
)

#: Where `build_report_variants` writes the signed `--report` fixture and its re-signed variants,
#: inside the canonical fixture directory; a variant's REMOVED file names files to delete
#: (`"missing"`) or replace with a directory (`"directory"`).
REPORT_DIR = "report"
REPORT_VARIANTS = "report-signed"
SIGNER_TRUST_ROOT = "signer-trust-root.json"
REMOVED = "census-removed.json"
#: A marker value for `report_with`: replace the file with an empty directory.
DIRECTORY = object()

#: One representative per JSON type. A node is replaced by every representative whose type differs
#: from its own.
TYPE_SAMPLES: tuple[tuple[str, Any], ...] = (
    ("null", None),
    ("bool", True),
    ("int", 7),
    ("float", 1.5),
    ("string", "x"),
    ("array", []),
    ("object", {}),
)

#: A string every refusal that names an untrusted value must render without a language's own repr:
#: a quote, a backslash, non-ASCII and a control character.
SPECIAL_STRING = "it's \\ caf\u00e9\n"

#: The fields the engines base64-decode. Each also gets a value with its padding stripped (Java's
#: default decoder accepts that; Python's and Node's strict reading must not), one with a character
#: outside the alphabet, and well-formed base64 of the wrong length for a key or signature.
BASE64_FIELDS = frozenset({"payload", "sig", "signature", "public_key"})

#: Number tokens JSON parsers disagree on: non-standard constants, integral floats, exponents,
#: negative zero, a double overflow and an integer past 64 bits.
NUMBER_TOKENS = (
    "NaN",
    "Infinity",
    "-Infinity",
    "1.0",
    "1e2",
    "-0.0",
    "1e400",
    "18446744073709551616",
)

#: String escapes the engines' JSON readers and canonical writers must treat alike.
STRING_TOKENS = (
    ("lone-surrogate", '"\\ud800"'),
    ("nul-escape", '"\\u0000"'),
    ("astral", '"\\ud83d\\ude00"'),
)


def path_names_for(base_name: str) -> tuple[tuple[str, str], ...]:
    """Path values for a manifest entry that names a real file `base_name` inside the fixture
    directory built by `bundle_with`/`bundle_evidence_with` (which also provides `outside.txt` one
    level up, a `subdir`, and `link-out`/`link-in`): escapes, links, odd spellings of the real name,
    and a directory."""
    return (
        ("absolute", "/etc/hosts"),
        ("dotdot", "../outside.txt"),
        ("dotdot-back-in", f"sub/../{base_name}"),
        ("dot-prefix", f"./{base_name}"),
        ("trailing-slash", f"{base_name}/"),
        ("nul", f"{base_name}\u0000x"),
        ("empty", ""),
        ("dot", "."),
        ("directory", "subdir"),
        ("backslash", "..\\outside.txt"),
        ("link-out", "link-out"),
        ("link-in", "link-in"),
        ("non-ascii", "artifact-é.txt"),
    )


#: Artifact names: escapes, links, odd spellings of the real name, and a directory.
ARTIFACT_NAMES = path_names_for("artifact-a.txt")

DEEP_NESTING = 100_000
#: The deepest container nesting the engines accept (Jackson's default, enforced alike in all three);
#: keep equal to `signing.MAX_JSON_DEPTH` (tools do not import the engine at module level).
MAX_DEPTH = 1000
#: The release statement's predicate type, as `agentce sign` writes it.
RELEASE_PREDICATE_TYPE = "https://agent-conformance.org/attestation/release/v1"


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def walk(
    value: Any, path: tuple[Any, ...] = ()
) -> Iterator[tuple[tuple[Any, ...], Any]]:
    """Every node below the root as `(path, value)`, parents before children, keys in sorted order."""
    if isinstance(value, dict):
        for key in sorted(value):
            yield (*path, key), value[key]
            yield from walk(value[key], (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield (*path, index), item
            yield from walk(item, (*path, index))


def pointer(path: tuple[Any, ...]) -> str:
    return "/" + "/".join(str(part) for part in path) if path else ""


def _copy_and_parent(document: Any, path: tuple[Any, ...]) -> tuple[Any, Any]:
    """A deep copy of `document`, and the container in it that holds the node at `path`."""
    result = copy.deepcopy(document)
    parent = result
    for part in path[:-1]:
        parent = parent[part]
    return result, parent


def replaced(document: Any, path: tuple[Any, ...], value: Any) -> Any:
    result, parent = _copy_and_parent(document, path)
    parent[path[-1]] = value
    return result


def deleted(document: Any, path: tuple[Any, ...]) -> Any:
    result, parent = _copy_and_parent(document, path)
    del parent[path[-1]]
    return result


def node_mutations(
    document: Any, *, skip: Callable[[tuple[Any, ...]], bool] = lambda _: False
) -> Iterator[tuple[str, str, Any]]:
    """`(name, problem_class, mutated_document)` for every node: each other JSON type, and deletion."""
    for path, value in walk(document):
        if skip(path):
            continue
        own = json_type(value)
        for type_name, sample in TYPE_SAMPLES:
            if type_name != own:
                yield (
                    f"{pointer(path)}={type_name}",
                    "json-type",
                    replaced(document, path, sample),
                )
        if value != SPECIAL_STRING:
            yield (
                f"{pointer(path)}=special-string",
                "json-type",
                replaced(document, path, SPECIAL_STRING),
            )
        yield f"{pointer(path)}-deleted", "missing-field", deleted(document, path)
        if path and path[-1] in BASE64_FIELDS and isinstance(value, str) and value:
            unpadded = value.rstrip("=") if value.endswith("=") else value[:-1]
            yield (
                f"{pointer(path)}=base64-unpadded",
                "base64",
                replaced(document, path, unpadded),
            )
            yield (
                f"{pointer(path)}=base64-bad-character",
                "base64",
                replaced(document, path, "!" + value[1:]),
            )
            yield (
                f"{pointer(path)}=base64-wrong-length",
                "base64",
                replaced(document, path, base64.b64encode(bytes(31)).decode("ascii")),
            )


def _inject(original: bytes, token: str) -> bytes:
    """`original` (a JSON object or array) with `token` added as its first member or element."""
    text = original.decode("utf-8").lstrip()
    if text.startswith("{"):
        return ('{"census-extra": ' + token + ", " + text[1:]).encode("utf-8")
    return ("[" + token + ", " + text[1:]).encode("utf-8")


def byte_mutations(original: bytes) -> Iterator[tuple[str, str, bytes]]:
    """`(name, problem_class, new_bytes)` for one JSON file: every byte-level problem class."""
    yield "empty", "bytes", b""
    yield "truncated", "bytes", original[: len(original) // 2]
    yield "bom", "bytes", b"\xef\xbb\xbf" + original
    yield "non-utf8-lead", "bytes", b"\xff" + original
    yield (
        "non-utf8-in-string",
        "bytes",
        _inject(original, '"x"').replace(b'"x"', b'"\xff"', 1),
    )
    yield "trailing-garbage", "bytes", original + b" x"
    yield "nesting", "nesting", b"[" * DEEP_NESTING + b"]" * DEEP_NESTING
    for name, depth in (("at-limit", MAX_DEPTH), ("past-limit", MAX_DEPTH + 1)):
        yield f"nesting-{name}", "nesting", b"[" * depth + b"]" * depth
        # The root container is one level, so the injected array sits at the same total depth.
        inner = "[" * (depth - 1) + "]" * (depth - 1)
        yield f"nesting-inside-{name}", "nesting", _inject(original, inner)
    yield (
        "nesting-inside",
        "nesting",
        _inject(original, "[" * DEEP_NESTING + "]" * DEEP_NESTING),
    )
    for token in NUMBER_TOKENS:
        yield f"number-{token}", "number-token", _inject(original, token)
    for name, token in STRING_TOKENS:
        yield f"string-{name}", "bytes", _inject(original, token)
    # A duplicate of the first key of the root object, or of the first entry's when the root is an
    # array of objects (signatures.json).
    text = original.decode("utf-8").lstrip()
    document = json.loads(text)
    prefix = "{"
    if isinstance(document, list) and document and isinstance(document[0], dict):
        document, prefix = document[0], "[{"
        text = "[" + text[1:].lstrip()
    if isinstance(document, dict) and document:
        first_key = json.dumps(sorted(document)[0])
        yield (
            "duplicate-key",
            "duplicate-key",
            (prefix + first_key + ": 1, " + text[len(prefix) :]).encode(),
        )


@dataclass(frozen=True)
class Mutation:
    name: str
    flow: str
    problem_class: str
    target: (
        str  # "catalog", "release", "bundle", or "report" -- which verify flag to pass
    )
    #: Writes this mutation's fixture into an empty per-engine directory and returns the path to pass
    #: to `verify --catalog`/`--release`/`--bundle`/`--report`. Reads only the canonical fixtures.
    build: Callable[[Path], Path]
    #: Overrides the argv value computed from `build`'s returned path (default: `str(path)`) --
    #: `target-argument` mutations build a valid fixture but pass a different spelling of its path.
    #: A list replaces the whole `<flag> <value>` pair (a repeated flag, the `--flag=value` form).
    arg: Callable[[Path], str | list[str]] | None = None


_T = TypeVar("_T")


def _bound(
    argv: Callable[[Path, _T], list[str]], value: _T
) -> Callable[[Path], list[str]]:
    """`argv` with its second argument fixed now, not when the mutation runs (a loop variable)."""
    return lambda path: argv(path, value)


def _write(path: Path, data: bytes | Any) -> None:
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(data), encoding="utf-8")


def generate(canonical: Path, catalog: Path, evidence_bundle: Path) -> list[Mutation]:
    """Every mutation the census generates, from the canonical fixtures in `canonical` (built by
    `verify_parity_check.build_canonical_fixtures`), the real signed catalog at `catalog`, and the
    real signed evidence bundle at `evidence_bundle` (`corpus/quickstart/evidence`).

    The result is a platform-independent mutation list: it depends on those three inputs only, never
    on the machine or its environment, so a `--list-shard` run on a laptop lists what CI's shards
    run. The fixture builder names the report's formats rather than letting the `CI` variable pick
    them, and VG-VERIFY-CENSUS-SHARD-COVERAGE compares the list built with and without `CI`."""
    mutations: list[Mutation] = []

    def add(
        name: str,
        flow: str,
        problem_class: str,
        build: Callable[[Path], Path],
        *,
        target: str | None = None,
        arg: Callable[[Path], str | list[str]] | None = None,
    ) -> None:
        if target is None:
            target = "catalog" if flow.startswith("catalog-") else "release"
        mutations.append(
            Mutation(f"{flow}:{name}", flow, problem_class, target, build, arg)
        )

    def catalog_with(edit: Callable[[Path], object]) -> Callable[[Path], Path]:
        def build(d: Path) -> Path:
            dest = d / "catalog"
            shutil.copytree(catalog, dest, symlinks=True)
            edit(dest)
            return dest

        return build

    def single_file(data: bytes | Any) -> Callable[[Path], Path]:
        def build(d: Path) -> Path:
            dest = d / "release.json"
            _write(dest, data)
            return dest

        return build

    def bundle_with(edit: Callable[[Path], object]) -> Callable[[Path], Path]:
        def build(d: Path) -> Path:
            dest = d / "bundle"
            shutil.copytree(canonical / "bundle", dest, symlinks=True)
            (d / "outside.txt").write_bytes((dest / "artifact-a.txt").read_bytes())
            (dest / "subdir").mkdir()
            os.symlink(d / "outside.txt", dest / "link-out")
            os.symlink("artifact-a.txt", dest / "link-in")
            edit(dest)
            return dest

        return build

    def bundle_evidence_with(edit: Callable[[Path], object]) -> Callable[[Path], Path]:
        def build(d: Path) -> Path:
            dest = d / "evidence"
            shutil.copytree(evidence_bundle, dest, symlinks=True)
            real_file = dest / "events" / "_benign-quarantine.jsonl"
            (d / "outside.txt").write_bytes(real_file.read_bytes())
            (dest / "subdir").mkdir()
            os.symlink(d / "outside.txt", dest / "link-out")
            os.symlink("events/_benign-quarantine.jsonl", dest / "link-in")
            edit(dest)
            return dest

        return build

    def set_file(name: str, data: bytes | Any) -> Callable[[Path], None]:
        return lambda dest: _write(dest / name, data)

    def make_unreadable(name: str) -> Callable[[Path], None]:
        return lambda dest: (dest / name).chmod(0)

    def replace_with_directory(name: str) -> Callable[[Path], None]:
        def edit(dest: Path) -> None:
            (dest / name).unlink()
            (dest / name).mkdir()

        return edit

    # catalog-sig-file / catalog-envelope.
    sig_bytes = (catalog / CATALOG_SIGNATURE_NAME).read_bytes()
    for name, cls, data in byte_mutations(sig_bytes):
        add(
            name,
            "catalog-sig-file",
            cls,
            catalog_with(set_file(CATALOG_SIGNATURE_NAME, data)),
        )
    add(
        "directory",
        "catalog-sig-file",
        "not-a-file",
        catalog_with(replace_with_directory(CATALOG_SIGNATURE_NAME)),
    )
    add(
        "unreadable",
        "catalog-sig-file",
        "unreadable",
        catalog_with(make_unreadable(CATALOG_SIGNATURE_NAME)),
    )
    for name, cls, doc in node_mutations(json.loads(sig_bytes)):
        add(
            name,
            "catalog-envelope",
            cls,
            catalog_with(set_file(CATALOG_SIGNATURE_NAME, doc)),
        )

    # catalog-tree.
    content_file = sorted(
        p.name for p in catalog.iterdir() if p.name != CATALOG_SIGNATURE_NAME
    )[0]
    add(
        "content-changed",
        "catalog-tree",
        "content",
        catalog_with(lambda dest: (dest / content_file).write_bytes(b"changed\n")),
    )
    for name, file_name in (
        ("bmp-high", "！.txt"),
        ("astral", "\U0001f600.txt"),
        ("upper-vs-lower", "Zz.txt"),
    ):
        add(
            f"extra-file-{name}",
            "catalog-tree",
            "file-name-order",
            catalog_with(set_file(file_name, b"x\n")),
        )
    add(
        "empty-subdirectory",
        "catalog-tree",
        "empty-directory",
        catalog_with(lambda dest: (dest / "empty").mkdir()),
    )
    add(
        "link-out",
        "catalog-tree",
        "link",
        catalog_with(lambda dest: os.symlink(dest.parent, dest / "link-out")),
    )
    add(
        "link-to-file",
        "catalog-tree",
        "link",
        catalog_with(lambda dest: os.symlink(content_file, dest / "link-in")),
    )

    # catalog-statement / release-statement / certificate: the re-signed variants.
    classes = json.loads(
        (canonical / "signed" / "classes.json").read_text(encoding="utf-8")
    )
    for stem, cls in sorted(classes.items()):
        flow, _, name = stem.partition("__")
        envelope = json.loads(
            (canonical / "signed" / f"{stem}.json").read_text(encoding="utf-8")
        )
        if flow == "catalog-statement":
            add(
                name,
                flow,
                cls,
                catalog_with(set_file(CATALOG_SIGNATURE_NAME, envelope)),
            )
        elif flow == "release-statement":

            def edit(dest: Path, envelope: Any = envelope) -> None:
                entries = json.loads(
                    (dest / "signatures.json").read_text(encoding="utf-8")
                )
                entries[0]["envelope"] = envelope
                _write(dest / "signatures.json", entries)

            add(name, flow, cls, bundle_with(edit))
        else:
            add(name, flow, cls, single_file(envelope))

    # release-file / release-envelope (kms and certificate envelopes).
    for source in ("release-kms.json", "release-cert.json"):
        raw = (canonical / source).read_bytes()
        stem = source.removesuffix(".json")
        for name, cls, data in byte_mutations(raw):
            add(f"{stem}:{name}", "release-file", cls, single_file(data))

        def unreadable(d: Path, raw: bytes = raw) -> Path:
            dest = single_file(raw)(d)
            dest.chmod(0)
            return dest

        add(f"{stem}:unreadable", "release-file", "unreadable", unreadable)
        for name, cls, doc in node_mutations(json.loads(raw)):
            add(f"{stem}:{name}", "release-envelope", cls, single_file(doc))

    # manifest-file / manifest-fields / artifact-path.
    manifest_bytes = (canonical / "bundle" / "release-manifest.json").read_bytes()
    for name, cls, data in byte_mutations(manifest_bytes):
        add(
            name,
            "manifest-file",
            cls,
            bundle_with(set_file("release-manifest.json", data)),
        )
    add(
        "directory",
        "manifest-file",
        "not-a-file",
        bundle_with(replace_with_directory("release-manifest.json")),
    )
    add(
        "unreadable",
        "manifest-file",
        "unreadable",
        bundle_with(make_unreadable("release-manifest.json")),
    )
    manifest = json.loads(manifest_bytes)
    for name, cls, doc in node_mutations(manifest):
        add(
            name,
            "manifest-fields",
            cls,
            bundle_with(set_file("release-manifest.json", doc)),
        )
    for name, artifact_name in ARTIFACT_NAMES:
        doc = replaced(manifest, ("artifacts", 0, "name"), artifact_name)
        add(
            name,
            "artifact-path",
            "path",
            bundle_with(set_file("release-manifest.json", doc)),
        )
    add(
        "content-changed",
        "artifact-path",
        "content",
        bundle_with(set_file("artifact-a.txt", b"changed\n")),
    )
    add(
        "artifact-is-directory",
        "artifact-path",
        "content",
        bundle_with(replace_with_directory("artifact-a.txt")),
    )
    # As root a mode-000 file still reads: the rows stay so the list is the same on every machine,
    # and run_census skips only their expected-key assertion (18.68).
    add(
        "artifact-unreadable",
        "artifact-path",
        "unreadable",
        bundle_with(make_unreadable("artifact-a.txt")),
    )
    add(
        "unreadable-file",
        "catalog-tree",
        "unreadable",
        catalog_with(make_unreadable(content_file)),
    )
    content_dir = sorted(p.name for p in catalog.iterdir() if p.is_dir())[0]
    add(
        "unreadable-subdirectory",
        "catalog-tree",
        "unreadable",
        catalog_with(make_unreadable(content_dir)),
    )
    add(
        "broken-link",
        "catalog-tree",
        "link",
        catalog_with(lambda dest: os.symlink("no-such-file", dest / "broken")),
    )

    # signatures-file / signature-entries.
    signatures_bytes = (canonical / "bundle" / "signatures.json").read_bytes()
    for name, cls, data in byte_mutations(signatures_bytes):
        add(
            name,
            "signatures-file",
            cls,
            bundle_with(set_file("signatures.json", data)),
        )
    add(
        "directory",
        "signatures-file",
        "not-a-file",
        bundle_with(replace_with_directory("signatures.json")),
    )
    add(
        "unreadable",
        "signatures-file",
        "unreadable",
        bundle_with(make_unreadable("signatures.json")),
    )
    for name, cls, doc in node_mutations(json.loads(signatures_bytes)):
        add(
            name,
            "signature-entries",
            cls,
            bundle_with(set_file("signatures.json", doc)),
        )

    entries = json.loads(signatures_bytes)
    for type_name, sample in (*TYPE_SAMPLES, ("special-string", SPECIAL_STRING)):
        broken = replaced(entries, (0, "profile"), sample)
        del broken[0]["envelope"]
        add(
            f"/0/profile={type_name}-with-no-envelope",
            "signature-entries",
            "json-type",
            bundle_with(set_file("signatures.json", broken)),
        )

    # bundle-manifest / bundle-files (--bundle's own manifest.json and the files it names).
    bundle_manifest_bytes = (evidence_bundle / "manifest.json").read_bytes()
    for name, cls, data in byte_mutations(bundle_manifest_bytes):
        add(
            name,
            "bundle-manifest",
            cls,
            bundle_evidence_with(set_file("manifest.json", data)),
            target="bundle",
        )
    add(
        "directory",
        "bundle-manifest",
        "not-a-file",
        bundle_evidence_with(replace_with_directory("manifest.json")),
        target="bundle",
    )
    add(
        "unreadable",
        "bundle-manifest",
        "unreadable",
        bundle_evidence_with(make_unreadable("manifest.json")),
        target="bundle",
    )
    bundle_manifest = json.loads(bundle_manifest_bytes)
    for name, cls, doc in node_mutations(bundle_manifest):
        add(
            name,
            "bundle-manifest",
            cls,
            bundle_evidence_with(set_file("manifest.json", doc)),
            target="bundle",
        )
    # A `sources[].id` wrapped in a single-element array holding its own real value: Python's
    # `str(["x"])` ("['x']"), TypeScript's `String(["x"])` ("x", JS array-to-string joins a
    # single-element array to its bare element) and Java's Jackson `asText()` ("") each render this
    # differently, and only TypeScript's happens to still equal the real id -- so unlike every other
    # node_mutations case above, an arbitrary stand-in value can't expose this; the mutation has to
    # wrap the id this manifest actually declares (verifier round 1, 18.65).
    wrapped_sources = [
        {**source, "id": [source["id"]]}
        if isinstance(source.get("id"), str)
        else source
        for source in bundle_manifest.get("sources", [])
    ]
    add(
        "source-id-wrapped-in-array",
        "bundle-manifest",
        "source-id",
        bundle_evidence_with(
            set_file(
                "manifest.json",
                replaced(bundle_manifest, ("sources",), wrapped_sources),
            )
        ),
        target="bundle",
    )
    bundle_file_name = "events/_benign-quarantine.jsonl"
    for name, path_value in path_names_for(bundle_file_name):
        doc = replaced(bundle_manifest, ("files", 0, "path"), path_value)
        add(
            name,
            "bundle-files",
            "path",
            bundle_evidence_with(set_file("manifest.json", doc)),
            target="bundle",
        )
    add(
        "content-changed",
        "bundle-files",
        "content",
        bundle_evidence_with(
            lambda dest: (dest / bundle_file_name).write_bytes(b"changed\n")
        ),
        target="bundle",
    )
    add(
        "missing-file",
        "bundle-files",
        "content",
        bundle_evidence_with(lambda dest: (dest / bundle_file_name).unlink()),
        target="bundle",
    )
    add(
        "unreadable-file",
        "bundle-files",
        "unreadable",
        bundle_evidence_with(make_unreadable(bundle_file_name)),
        target="bundle",
    )
    add(
        "unreadable-events-directory",
        "bundle-files",
        "unreadable",
        bundle_evidence_with(make_unreadable("events")),
        target="bundle",
    )
    add(
        "unreadable-stream",
        "bundle-streams",
        "unreadable",
        bundle_evidence_with(make_unreadable(STREAM_FILE)),
        target="bundle",
    )

    # bundle-streams (each events/*.jsonl line ingest reads). The first line of one real stream is
    # replaced, or the file's line structure changed, and manifest.json's digest for that file is
    # updated so ingest's own reading is what decides the outcome, not the content check.
    stream_bytes = (evidence_bundle / STREAM_FILE).read_bytes()
    first_line, _, rest = stream_bytes.partition(b"\n")

    def bundle_stream(data: bytes) -> Callable[[Path], Path]:
        def edit(dest: Path) -> None:
            (dest / STREAM_FILE).write_bytes(data)
            manifest = json.loads((dest / "manifest.json").read_bytes())
            for entry in manifest["files"]:
                if entry["path"] == STREAM_FILE:
                    entry["sha256"] = hashlib.sha256(data).hexdigest()
            _write(dest / "manifest.json", manifest)

        return bundle_evidence_with(edit)

    for name, cls, line in byte_mutations(first_line):
        add(
            name,
            "bundle-streams",
            cls,
            bundle_stream(line + b"\n" + rest),
            target="bundle",
        )
    for name, sample in TYPE_SAMPLES:
        if name != "object":
            line = json.dumps(sample).encode()
            add(
                f"line={name}",
                "bundle-streams",
                "json-type",
                bundle_stream(line + b"\n" + rest),
                target="bundle",
            )
    for name, data in (
        ("crlf", stream_bytes.replace(b"\n", b"\r\n")),
        ("lone-cr", stream_bytes.replace(b"\n", b"\r")),
        ("cr-inside-line", first_line.replace(b",", b",\r", 1) + b"\n" + rest),
        ("no-final-newline", stream_bytes.rstrip(b"\n")),
        ("blank-lines", b"\n\n" + stream_bytes.replace(b"\n", b"\n\n")),
        ("vertical-tab", first_line + b"\x0b\n" + rest),
        ("form-feed", first_line + b"\x0c\n" + rest),
        (
            "unicode-line-separator",
            first_line.replace(b",", b",\xe2\x80\xa8", 1) + b"\n" + rest,
        ),
        ("next-line", first_line.replace(b",", b",\xc2\x85", 1) + b"\n" + rest),
    ):
        add(name, "bundle-streams", "line-break", bundle_stream(data), target="bundle")
    for name, prefix in WHITESPACE_PREFIXES:
        add(
            name,
            "bundle-streams",
            "whitespace",
            bundle_stream(prefix + first_line + b"\n" + rest),
            target="bundle",
        )

    # target-argument (verify's own --catalog/--release/--bundle/--report value).
    def report_dir_unused(d: Path) -> Path:
        dest = d / "report-unused"
        dest.mkdir()
        return dest

    def arg_empty(_path: Path) -> str:
        return ""

    def arg_trailing_slash(path: Path) -> str:
        return str(path) + "/"

    def arg_dot_suffix(path: Path) -> str:
        return str(path) + "/."

    for flag_target, builder in (
        ("catalog", catalog_with(lambda _dest: None)),
        ("release", bundle_with(lambda _dest: None)),
        ("bundle", bundle_evidence_with(lambda _dest: None)),
    ):
        add(
            f"{flag_target}-empty",
            "target-argument",
            "empty",
            builder,
            target=flag_target,
            arg=arg_empty,
        )
        add(
            f"{flag_target}-trailing-slash",
            "target-argument",
            "trailing-slash",
            builder,
            target=flag_target,
            arg=arg_trailing_slash,
        )
        add(
            f"{flag_target}-dot",
            "target-argument",
            "dot",
            builder,
            target=flag_target,
            arg=arg_dot_suffix,
        )
    add(
        "report-empty",
        "target-argument",
        "empty",
        report_dir_unused,
        target="report",
        arg=arg_empty,
    )

    def target_unreadable(build: Callable[[Path], Path]) -> Callable[[Path], Path]:
        def unreadable(d: Path) -> Path:
            dest = build(d)
            dest.chmod(0)
            return dest

        return unreadable

    for flag_target, builder in (
        ("catalog", catalog_with(lambda _dest: None)),
        ("release", bundle_with(lambda _dest: None)),
        ("bundle", bundle_evidence_with(lambda _dest: None)),
    ):
        add(
            f"{flag_target}-unreadable",
            "target-argument",
            "unreadable",
            target_unreadable(builder),
            target=flag_target,
        )

    # The flag given twice (the same value, or one of them empty, in either order) and the
    # `--flag=value` spelling: one rule in all three engines (18.65 round 3).
    def repeated(first: Callable[[Path], str], second: Callable[[Path], str]):
        def argv(path: Path, flag: str) -> list[str]:
            return [flag, first(path), flag, second(path)]

        return argv

    for flag_target, builder in (
        ("catalog", catalog_with(lambda _dest: None)),
        ("release", bundle_with(lambda _dest: None)),
        ("bundle", bundle_evidence_with(lambda _dest: None)),
        ("report", report_dir_unused),
    ):
        flag = "--" + flag_target
        for name, argv in (
            ("twice", repeated(str, str)),
            ("twice-then-empty", repeated(str, arg_empty)),
            ("empty-then-twice", repeated(arg_empty, str)),
        ):
            add(
                f"{flag_target}-{name}",
                "target-argument",
                "repeated",
                builder,
                target=flag_target,
                arg=_bound(argv, flag),
            )
        if flag_target != "report":
            # TypeScript/Java parse `--report=<dir>` and then refuse --report as unported, so only
            # its empty form below has one answer to compare across the three engines.
            add(
                f"{flag_target}-equals",
                "target-argument",
                "equals",
                builder,
                target=flag_target,
                arg=_bound(lambda path, flag: [f"{flag}={path}"], flag),
            )
        add(
            f"{flag_target}-equals-empty",
            "target-argument",
            "equals",
            builder,
            target=flag_target,
            arg=_bound(lambda _path, flag: [f"{flag}="], flag),
        )
    # A trailing slash on a plain file (not a release-bundle directory) must refuse the same way a
    # nonexistent path does (F5, 18.65): `release-trailing-slash` above exercises a bundle
    # *directory*, which already ends in a real directory either way, so it never reaches the
    # raw-string `os.path.exists`-style check this row targets.
    add(
        "release-file-trailing-slash",
        "target-argument",
        "trailing-slash",
        single_file((canonical / "release-kms.json").read_bytes()),
        target="release",
        arg=arg_trailing_slash,
    )

    # verify's argv grammar (18.65 round 3): every token form argparse refuses or reads differently
    # from a hand scanner, each beside a valid evidence bundle so only the argv decides the answer.
    evidence_ok = bundle_evidence_with(lambda _dest: None)
    for name, cls, tokens in (
        ("bundle-missing-value", "missing-value", ["--bundle"]),
        ("bundle-value-is-a-flag", "missing-value", ["--bundle", "--json"]),
        (
            "signer-missing-value",
            "missing-value",
            ["--bundle", "{}", "--signer-trust-root"],
        ),
        ("keyid-missing-value", "missing-value", ["--bundle", "{}", "--expect-keyid"]),
        ("json-with-value", "takes-no-value", ["--bundle", "{}", "--json=1"]),
        ("quiet-with-value", "takes-no-value", ["--bundle", "{}", "--quiet="]),
        ("unknown-flag", "unknown-flag", ["--bundle", "{}", "--unknown"]),
        ("unknown-flag-equals", "unknown-flag", ["--bundle", "{}", "--unknown=1"]),
        ("unknown-short-flag", "unknown-flag", ["-x", "{}"]),
        ("unknown-before-missing", "unknown-flag", ["--unknown", "--bundle"]),
        ("abbreviated", "abbreviation", ["--bun", "{}"]),
        ("upper-case", "abbreviation", ["--BUNDLE", "{}"]),
        ("positional", "positional", ["--bundle", "{}", "extra"]),
        ("dash", "positional", ["--bundle", "{}", "-"]),
        ("negative-number", "positional", ["--bundle", "{}", "-1"]),
        ("double-dash-after", "double-dash", ["--bundle", "{}", "--"]),
        ("double-dash-before", "double-dash", ["--", "--bundle", "{}"]),
        ("signer-equals", "equals", ["--bundle", "{}", "--signer-trust-root=x"]),
        ("keyid-equals", "equals", ["--bundle", "{}", "--expect-keyid=k"]),
        ("keyid-equals-empty", "equals", ["--bundle", "{}", "--expect-keyid="]),
    ):
        add(
            name,
            "verify-argv",
            cls,
            evidence_ok,
            target="bundle",
            arg=_bound(lambda path, tokens: [t.format(path) for t in tokens], tokens),
        )

    mutations.extend(report_mutations(canonical))
    return mutations


def report_mutations(canonical: Path) -> list[Mutation]:
    """The `--report` rows, over the signed report `build_report_variants` wrote into `canonical`:
    claim.json and trust-root.json tampered in place (both are read before any signature check),
    and the re-signed statement, manifest.json and packaging.json variants it saved."""
    base = canonical / REPORT_DIR
    mutations: list[Mutation] = []

    def report_with(
        files: dict[str, bytes | Any | None], unreadable: str | None = None
    ) -> Callable[[Path], Path]:
        def build(d: Path) -> Path:
            dest = d / "report"
            shutil.copytree(base, dest, symlinks=True)
            for name, data in files.items():
                target = dest / name
                target.unlink(missing_ok=True)
                if data is DIRECTORY:
                    target.mkdir()
                elif data is not None:
                    _write(target, data)
            if unreadable is not None:
                (dest / unreadable).chmod(0)
            return dest

        return build

    def add(
        name: str, flow: str, cls: str, files: dict[str, bytes | Any | None]
    ) -> None:
        mutations.append(
            Mutation(f"{flow}:{name}", flow, cls, "report", report_with(files))
        )

    # A file or folder of the report made unreadable (chmod 000); "." is the report folder itself.
    for flow, name, unreadable in (
        ("report-claim", "unreadable", "claim.json"),
        ("report-claim", "report-directory-unreadable", "."),
        ("report-trust-root", "unreadable", "trust-root.json"),
        ("report-manifest", "unreadable", "manifest.json"),
        ("report-manifest", "unreadable-output", "coverage.json"),
        ("report-manifest", "unreadable-output-directory", "packs"),
        ("report-packaging", "unreadable", "packaging.json"),
        ("report-packaging", "unreadable-profile", "bundle/applicability.yaml"),
        ("report-packaging", "unreadable-domain", "bundle/domain.linkml.yaml"),
        ("report-packaging", "unreadable-evidence-directory", "bundle/evidence/events"),
    ):
        mutations.append(
            Mutation(
                f"{flow}:{name}",
                flow,
                "unreadable",
                "report",
                report_with({}, unreadable),
            )
        )

    for flow, file_name in (
        ("report-claim", "claim.json"),
        ("report-trust-root", "trust-root.json"),
    ):
        original = (base / file_name).read_bytes()
        for name, cls, data in byte_mutations(original):
            add(name, flow, cls, {file_name: data})
        for name, cls, doc in node_mutations(json.loads(original)):
            add(name, flow, cls, {file_name: doc})
        add("missing", flow, "not-a-file", {file_name: None})
        add("directory", flow, "not-a-file", {file_name: DIRECTORY})

    # `--signer-trust-root <file>`: the same loader, reached through argv instead of the report
    # directory. The report stays untouched; the external file (or its argv spelling) is mutated.
    def with_signer(
        name: str, cls: str, data: bytes | Any | None, spell: str = "{}"
    ) -> None:
        def build(d: Path) -> Path:
            dest = report_with({})(d)
            target = d / SIGNER_TRUST_ROOT
            if data is DIRECTORY:
                target.mkdir()
            elif data is not None:
                _write(target, data)
            return dest

        def arg(dest: Path) -> list[str]:
            value = spell.format(dest.parent / SIGNER_TRUST_ROOT)
            return ["--report", str(dest), "--signer-trust-root", value]

        mutations.append(
            Mutation(
                f"signer-trust-root:{name}",
                "signer-trust-root",
                cls,
                "report",
                build,
                arg,
            )
        )

    original = (base / "trust-root.json").read_bytes()
    for name, cls, data in byte_mutations(original):
        with_signer(name, cls, data)
    for name, cls, doc in node_mutations(json.loads(original)):
        with_signer(name, cls, doc)
    with_signer("missing", "not-a-file", None)
    with_signer("directory", "not-a-file", DIRECTORY)
    with_signer("dot", "not-a-file", original, ".")
    with_signer("empty", "empty", original, "")
    with_signer("trailing-slash", "trailing-slash", original, "{}/")

    def signer_unreadable(d: Path) -> Path:
        dest = report_with({})(d)
        _write(d / SIGNER_TRUST_ROOT, original)
        (d / SIGNER_TRUST_ROOT).chmod(0)
        return dest

    mutations.append(
        Mutation(
            "signer-trust-root:unreadable",
            "signer-trust-root",
            "unreadable",
            "report",
            signer_unreadable,
            lambda dest: [
                "--report",
                str(dest),
                "--signer-trust-root",
                str(dest.parent / SIGNER_TRUST_ROOT),
            ],
        )
    )

    variants = canonical / REPORT_VARIANTS
    classes = json.loads((variants / "classes.json").read_text(encoding="utf-8"))
    for stem, (flow, cls) in sorted(classes.items()):
        files: dict[str, bytes | Any | None] = {
            f.name: f.read_bytes() for f in sorted((variants / stem).iterdir())
        }
        for name, how in json.loads(cast(bytes, files.pop(REMOVED, b"{}"))).items():
            files[name] = DIRECTORY if how == "directory" else None
        add(stem.split("__", 1)[1], flow, cls, files)
    return mutations


# --- The re-signed variants (run inside engines/python's environment by the fixture builder). ------


def statement_variants(
    statement: dict[str, Any],
) -> Iterator[tuple[str, str, bytes | Any]]:
    """`(name, problem_class, payload)` for a signed statement: every node mutation (a JSON value,
    canonicalized and signed), plus payload bytes that are not a JSON object at all."""
    yield from node_mutations(statement)
    yield "payload-not-json", "payload-bytes", b"{"
    yield "payload-non-utf8", "payload-bytes", b'{"_type": "\xff"}'
    yield "payload-array", "payload-bytes", b"[]"
    yield "payload-nesting", "payload-bytes", b"[" * DEEP_NESTING + b"]" * DEEP_NESTING
    yield "payload-nan", "payload-bytes", b'{"subject": NaN}'


def certificate_variants(cert: dict[str, Any]) -> Iterator[tuple[str, str, Any]]:
    """`(name, problem_class, body)` for a certificate: every node mutation of its signed body (the
    authority re-signs it, so the engines get past the authority's signature to the field checks).
    `issuer` is skipped: changing it only selects a different (or no) authority, which
    `release-envelope`'s own walk already covers."""
    body = {k: v for k, v in cert.items() if k != "signature"}
    yield from node_mutations(body, skip=lambda path: path[0] == "issuer")
    for name, _, change in _CERTIFICATE_FIELD_ROWS:
        yield name, "certificate-field", {**body, **change(body)}


#: The `certificate-field` rows: each name, the outcome it must reach in Python, the reference (a
#: message key, or `verified`), so a row the signature collapse refuses first cannot pass as a field
#: check, and the field values it sets on a certificate body.
_CERTIFICATE_FIELD_ROWS: tuple[
    tuple[str, str, Callable[[dict[str, Any]], dict[str, Any]]], ...
] = (
    (
        "/algorithm=ecdsa-p256",
        "verify.certificate_algorithm",
        lambda b: {"algorithm": "ecdsa-p256"},
    ),
    (
        "/algorithm=ED25519",
        "verify.certificate_algorithm",
        lambda b: {"algorithm": "ED25519"},
    ),
    (
        "/not_before=space-separator",
        "verify.certificate_validity_malformed",
        lambda b: {"not_before": b["not_before"].replace("T", " ")},
    ),
    (
        "/not_before=impossible-date",
        "verify.certificate_validity_malformed",
        lambda b: {"not_before": "2026-02-30T00:00:00Z"},
    ),
    (
        "/not_after=utc-offset",
        "verify.certificate_validity_malformed",
        lambda b: {"not_after": b["not_after"].removesuffix("Z") + "+00:00"},
    ),
    (
        "/not_after=impossible-date",
        "verify.certificate_validity_malformed",
        lambda b: {"not_after": "2027-13-01T00:00:00Z"},
    ),
    (
        "window=swapped",
        "verify.certificate_validity_inverted",
        lambda b: {"not_before": b["not_after"], "not_after": b["not_before"]},
    ),
    ("window=equal", "verified", lambda b: {"not_after": b["not_before"]}),
    (
        "timestamps=lowercase-t-z",
        "verified",
        lambda b: {
            "not_before": b["not_before"].lower(),
            "not_after": b["not_after"].lower(),
        },
    ),
)

#: The expected outcome of each `certificate-field` row, keyed by its census name as listed.
CERTIFICATE_FIELD_EXPECTED = {
    "certificate:" + name.replace("/", "."): expected
    for name, expected, _ in _CERTIFICATE_FIELD_ROWS
}


def build_signed_variants(
    out: Path,
    signing: Any,
    dev_trust: Any,
    canonicalize: Any,
    manifest_digest: str,
    catalog: Path,
) -> None:
    """Writes every re-signed variant to `out/signed/<flow>__<name>.json` and their problem classes
    to `out/signed/classes.json`. `signing`, `dev_trust` and `canonicalize` are passed in so this
    module never imports cryptography itself."""
    signed = out / "signed"
    signed.mkdir()
    classes: dict[str, str] = {}

    def envelope_over(payload: bytes, signer: Any) -> dict[str, Any]:
        sig = signer.sign(signing._pae(signing.INTOTO_PAYLOAD_TYPE, payload))
        entry: dict[str, Any] = {"keyid": signer.keyid, "sig": signing._b64e(sig)}
        if signer.certificate() is not None:
            entry["cert"] = signer.certificate()
        return {
            "payloadType": signing.INTOTO_PAYLOAD_TYPE,
            "payload": signing._b64e(payload),
            "signatures": [entry],
        }

    def payload_bytes(value: bytes | Any) -> bytes | None:
        if isinstance(value, bytes):
            return value
        try:
            return canonicalize(value)
        except ValueError:
            return None  # a float the signer itself refuses; the unsigned walks cover that class

    def save(flow: str, name: str, cls: str, envelope: dict[str, Any]) -> None:
        stem = f"{flow}__{name.replace('/', '.')}"
        (signed / f"{stem}.json").write_text(json.dumps(envelope), encoding="utf-8")
        classes[stem] = cls

    catalog_env = json.loads(
        (catalog / CATALOG_SIGNATURE_NAME).read_text(encoding="utf-8")
    )
    catalog_statement = json.loads(signing._b64d(catalog_env["payload"]))
    catalog_signer = dev_trust.KmsSigner(private_key=dev_trust.catalog_key())
    for name, cls, value in statement_variants(catalog_statement):
        payload = payload_bytes(value)
        if payload is not None:
            save("catalog-statement", name, cls, envelope_over(payload, catalog_signer))

    release_statement = signing.intoto_statement(
        subject_name="release-manifest.json",
        digest=manifest_digest,
        predicate_type=RELEASE_PREDICATE_TYPE,
        predicate={"profile": "kms", "release": "agentce@parity-fixture"},
    )
    kms = dev_trust.KmsSigner(private_key=dev_trust.kms_key())
    for name, cls, value in statement_variants(release_statement):
        payload = payload_bytes(value)
        if payload is not None:
            save("release-statement", name, cls, envelope_over(payload, kms))

    keyless = dev_trust.deterministic_keyless_signer("c3-census", "sigstore-public")
    authority = dev_trust.ca_key(keyless.cert["issuer"])
    single_statement = signing.intoto_statement(
        subject_name="release",
        digest="sha256:" + "0" * 64,
        predicate_type=RELEASE_PREDICATE_TYPE,
        predicate={},
    )
    for name, cls, body in certificate_variants(keyless.cert):
        try:
            signature = authority.sign(canonicalize(body))
        except ValueError:
            continue
        signer = type(keyless)(
            private_key=keyless.private_key,
            cert={**body, "signature": signing._b64e(signature)},
        )
        save(
            "certificate",
            name,
            cls,
            envelope_over(canonicalize(single_statement), signer),
        )
    (signed / "classes.json").write_text(
        json.dumps(classes, sort_keys=True), encoding="utf-8"
    )


def build_report_variants(
    out: Path, signing: Any, canonicalize: Any, signer: Any
) -> None:
    """Writes the re-signed `--report` variants of the signed report at `out/REPORT_DIR` to
    `out/REPORT_VARIANTS/<flow>__<name>/` (only the files that change) and their classes to
    `classes.json`. `signer` holds the key the report's embedded trust-root.json names, as anyone
    who writes that file holds theirs, so every variant gets past the signature check to the
    reader behind it."""
    base = out / REPORT_DIR
    variants = out / REPORT_VARIANTS
    variants.mkdir()
    classes: dict[str, list[str]] = {}
    claim = json.loads((base / "claim.json").read_bytes())
    entry = next(s for s in claim["signatures"] if s.get("role") == "claimant")
    statement = json.loads(signing._b64d(entry["payload"]))
    manifest_bytes = (base / "manifest.json").read_bytes()

    def save(
        flow: str,
        name: str,
        cls: str,
        *,
        statement_doc: Any = None,
        manifest: bytes | None = None,
        packaging: bytes | None = None,
        removed: dict[str, str] | None = None,
    ) -> None:
        files: dict[str, bytes] = {}
        if packaging is not None or removed:
            doc = json.loads(manifest_bytes)
            if packaging is not None:
                doc["outputs"]["packaging.json"] = signing.sha256_prefixed(packaging)
                files["packaging.json"] = packaging
            else:
                del doc["outputs"]["packaging.json"]
            manifest = json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"
        if manifest is not None:
            files["manifest.json"] = manifest
            statement_doc = copy.deepcopy(statement)
            for subject in statement_doc["subject"]:
                if subject["name"] == "manifest.json":
                    subject["digest"]["sha256"] = hashlib.sha256(manifest).hexdigest()
        try:
            canonicalize(statement_doc)
        except ValueError:
            return  # a float the signer itself refuses; the unsigned claim walk covers that
        envelope = signing.sign_statement(statement_doc, signer)
        signed_claim = {
            **claim,
            "signatures": [{"role": "claimant", "profile": "kms", **envelope}],
        }
        files["claim.json"] = json.dumps(signed_claim, sort_keys=True).encode()
        if removed:
            files[REMOVED] = json.dumps(removed).encode()
        stem = f"{flow}__{name.replace('/', '.')}"
        (variants / stem).mkdir()
        for file_name, data in files.items():
            (variants / stem / file_name).write_bytes(data)
        classes[stem] = [flow, cls]

    for name, cls, doc in node_mutations(statement):
        save("report-statement", name, cls, statement_doc=doc)
    for name, cls, data in byte_mutations(manifest_bytes):
        save("report-manifest", name, cls, manifest=data)
    for name, cls, doc in node_mutations(json.loads(manifest_bytes)):
        data = json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"
        save("report-manifest", name, cls, manifest=data)
    packaging_bytes = (base / "packaging.json").read_bytes()
    for name, cls, data in byte_mutations(packaging_bytes):
        save("report-packaging", name, cls, packaging=data)
    for name, cls, doc in node_mutations(json.loads(packaging_bytes)):
        data = json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"
        save("report-packaging", name, cls, packaging=data)
    for how in ("missing", "directory"):
        save(
            "report-packaging",
            how,
            "not-a-file",
            removed={"packaging.json": how},
        )
    (variants / "classes.json").write_text(
        json.dumps(classes, sort_keys=True), encoding="utf-8"
    )


# --- Census report and self-test. -------------------------------------------------------------------


# --- Surface inventory: derived from the Python reference's code, not a hand list -----------------

ROOT = Path(__file__).resolve().parent.parent
PYTHON_PACKAGE = ROOT / "engines" / "python" / "agentce"
VERIFY_ENTRY = "agentce.commands.cmd_verify"

#: Calls that read the filesystem or parse bytes. A call whose name is in this set, anywhere in a
#: function reachable from `cmd_verify`, is a read site the inventory must account for.
READ_CALLS = frozenset(
    {
        "read_bytes",
        "read_text",
        "open",
        "loads",
        "load",
        "safe_load",
        "iterdir",
        "rglob",
        "glob",
        "walk",
        "scandir",
        "listdir",
        "readlink",
        "stat",
        "lstat",
        "is_file",
        "is_dir",
        "exists",
        "isfile",
        "isdir",
        "islink",
        "getsize",
        "parse_untrusted_json",
    }
)


def load_sources(package: Path = PYTHON_PACKAGE) -> dict[str, str]:
    """Dotted path -> source text for every file of the Python reference; a package keeps its
    `.__init__` suffix so relative imports resolve against the right base."""
    return {
        ".".join(
            path.relative_to(package.parent).with_suffix("").parts
        ): path.read_text(encoding="utf-8")
        for path in sorted(package.rglob("*.py"))
    }


class _CallGraph:
    """Every function and method of the package, and an over-approximating call resolver: a call
    through an attribute it cannot pin to one definition resolves to every method of that name, so
    a read can only be over-reported, never missed."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.trees = {
            name.removesuffix(".__init__"): ast.parse(text, name)
            for name, text in sources.items()
        }
        self.packages = {
            name.removesuffix(".__init__")
            for name in sources
            if name.endswith(".__init__")
        }
        self.defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.module_of: dict[str, str] = {}
        self.methods: dict[str, list[str]] = {}
        for module, tree in self.trees.items():
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.defs[f"{module}.{node.name}"] = node
                    self.module_of[f"{module}.{node.name}"] = module
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            qual = f"{module}.{node.name}.{item.name}"
                            self.defs[qual] = item
                            self.module_of[qual] = module
                            self.methods.setdefault(item.name, []).append(qual)
        self.imports = {module: self._imports(module) for module in self.trees}

    def _imports(self, module: str) -> dict[str, str]:
        package = module if module in self.packages else module.rpartition(".")[0]
        names: dict[str, str] = {}
        for node in ast.walk(self.trees[module]):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    parts = parts[: len(parts) - (node.level - 1)]
                    base = ".".join([*parts, base] if base else parts)
                for alias in node.names:
                    names[alias.asname or alias.name] = f"{base}.{alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names[alias.asname or alias.name] = alias.name
        return names

    def callees(self, module: str, call: ast.Call) -> list[str]:
        func, imported = call.func, self.imports[module]
        if isinstance(func, ast.Name):
            found = [
                qual
                for qual in (
                    f"{module}.{func.id}",
                    imported.get(func.id, ""),
                    f"{module}.{func.id}.__init__",
                    imported.get(func.id, "") + ".__init__",
                )
                if qual in self.defs
            ]
            return found
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                target = imported.get(func.value.id, f"{module}.{func.value.id}")
                if f"{target}.{func.attr}" in self.defs:
                    return [f"{target}.{func.attr}"]
            return list(self.methods.get(func.attr, ()))
        return []


def _site_label(call: ast.Call) -> str:
    """A read site's stable name: the call as written, with its first argument (line numbers move
    with every edit; the expression only changes when the read itself does)."""
    first = ast.unparse(call.args[0]) if call.args else ""
    return f"{ast.unparse(call.func)}({first})"


def surface_inventory(sources: dict[str, str]) -> tuple[set[str], set[str]]:
    """The flags `agentce verify` accepts and every read site reachable from `cmd_verify`, both
    taken from the Python reference's own source."""
    graph = _CallGraph(sources)
    seen: set[str] = set()
    stack = [VERIFY_ENTRY]
    reads: set[str] = set()
    while stack:
        qual = stack.pop()
        if qual in seen:
            continue
        seen.add(qual)
        if qual in INVENTORY_BOUNDARIES:
            continue
        module = graph.module_of[qual]
        for node in ast.walk(graph.defs[qual]):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name in READ_CALLS:
                reads.add(f"{qual}: {_site_label(node)}")
            stack.extend(graph.callees(module, node))
    return _verify_flags(graph), reads


def _verify_flags(graph: _CallGraph) -> set[str]:
    """Option strings of the `verify` subparser in `build_parser`, plus its `parents=` flags."""
    flags: set[str] = set()
    in_verify = False
    for stmt in graph.defs["agentce.cli.build_parser"].body:
        call = getattr(stmt, "value", None)
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr == "add_parser":
            in_verify = bool(call.args) and ast.literal_eval(call.args[0]) == "verify"
            if in_verify and any(k.arg == "parents" for k in call.keywords):
                flags |= _option_strings(graph.defs["agentce.cli._common_flags"])
            if in_verify and not any(
                k.arg == "add_help" and ast.literal_eval(k.value) is False
                for k in call.keywords
            ):
                flags |= {"-h", "--help"}  # argparse adds them unless add_help=False
        elif call.func.attr == "add_argument" and in_verify:
            flags |= _option_strings(stmt)
    return flags


def _option_strings(node: ast.AST) -> set[str]:
    return {
        str(arg.value)
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_argument"
        for arg in call.args
        if isinstance(arg, ast.Constant) and str(arg.value).startswith("-")
    }


def inventory_problems(sources: dict[str, str]) -> list[str]:
    """A flag or read site with no census row or disposition, or an inventory entry the code no
    longer has. Each entry names census row ids, or is a string starting "disposition:"."""
    flags, reads = surface_inventory(sources)
    problems = []
    for kind, found, listed in (
        ("flag", flags, INVENTORY_FLAGS),
        ("read", reads, INVENTORY_READS),
    ):
        problems += [
            f"unlisted {kind}: {entry}" for entry in sorted(found - listed.keys())
        ]
        problems += [
            f"stale {kind}: {entry}" for entry in sorted(listed.keys() - found)
        ]
        for entry, target in sorted(listed.items()):
            if isinstance(target, str):
                if not target.startswith("disposition:"):
                    problems.append(
                        f"{kind} {entry}: a disposition must say so: {target}"
                    )
            else:
                problems += [
                    f"{kind} {entry} names no census row: {row}"
                    for row in target
                    if row not in FLOW_IDS
                ]
    return problems


#: Where the call-graph walk stops, and why. `_verify_report` re-runs `assess` in-process through
#: `cli.main`, which dispatches on `args.func` at runtime; the re-run reads only the packaged inputs
#: the `report-packaging` row's digest checks have already matched, and any error it raises is
#: contained by `main` and compared as output (`verify.report_reproduction_mismatch`).
INVENTORY_BOUNDARIES = {
    "agentce.cli.main": "the assess re-run: inputs digest-matched first, errors contained by main",
}

_OUTPUT_SWITCH = "disposition: an output switch; reads no input"
_HASHED_ONLY = (
    "disposition: bytes only hashed and compared to a signed digest; any content is a mismatch, "
    "never a parse"
)
_PACKAGED = "disposition: reads a file shipped inside the installed engine, not the verified input"
_IMPLICIT_HELP = (
    "disposition: argparse's implicit help; prints usage and reads no input (its argv spellings "
    "across engines are 18.52's)"
)

#: Every flag of `agentce verify`, mapped to the census rows whose mutations exercise it, or to a
#: written disposition. `inventory_problems` fails when the code grows a flag this map lacks.
INVENTORY_FLAGS: dict[str, tuple[str, ...] | str] = {
    "--bundle": ("target-argument", "bundle-manifest"),
    "--catalog": ("target-argument", "catalog-sig-file"),
    "--release": ("target-argument", "release-file", "manifest-file"),
    "--report": ("target-argument", "report-claim"),
    "--signer-trust-root": ("signer-trust-root", "verify-argv"),
    "--expect-keyid": ("verify-argv",),
    "--json": ("verify-argv",),
    "--debug": _OUTPUT_SWITCH,
    "--quiet": ("verify-argv",),
    "-h": _IMPLICIT_HELP,
    "--help": _IMPLICIT_HELP,
}

#: Every read site reachable from `cmd_verify` (`surface_inventory`), keyed by function and the call
#: as written, mapped to the census rows that mutate what it reads, or to a written disposition.
INVENTORY_READS: dict[str, tuple[str, ...] | str] = {
    "agentce.bundle._sha256_hex: path.open('rb')": ("bundle-files",),
    "agentce.bundle.permission_denied: path.stat()": (
        "bundle-manifest",
        "bundle-files",
    ),
    "agentce.bundle.load_bundle: manifest_path.read_bytes()": ("bundle-manifest",),
    "agentce.bundle.load_bundle: member.stat()": ("bundle-files",),
    "agentce.bundle.load_bundle: parse_untrusted_json(manifest_path.read_bytes())": (
        "bundle-manifest",
    ),
    "agentce.bundle.safe_is_file: path.is_file()": ("bundle-manifest", "bundle-files"),
    "agentce.commands._load_release_json: path.read_bytes()": (
        "release-file",
        "manifest-file",
        "signatures-file",
    ),
    "agentce.commands._load_release_json: signing.parse_untrusted_json(path.read_bytes())": (
        "release-file",
        "manifest-file",
        "signatures-file",
    ),
    "agentce.commands._load_report_trust_root: os.path.isfile(path)": (
        "report-trust-root",
        "signer-trust-root",
    ),
    "agentce.commands._parse_untrusted_object: signing.parse_untrusted_json(data)": (
        "report-claim",
        "report-manifest",
        "report-packaging",
    ),
    "agentce.commands._require_dir: path.is_dir()": ("target-argument",),
    "agentce.commands._verify_release: artifact_file.read_bytes()": ("artifact-path",),
    "agentce.commands._verify_release: manifest_path.is_file()": ("manifest-file",),
    "agentce.commands._verify_release: path.stat()": (
        "manifest-file",
        "signatures-file",
    ),
    "agentce.commands._verify_release: release_path.is_file()": ("release-file",),
    "agentce.commands._verify_release: signatures_path.is_file()": ("signatures-file",),
    "agentce.commands._verify_report: candidate_path.is_file()": ("report-manifest",),
    "agentce.commands._verify_report: candidate_path.read_bytes()": _HASHED_ONLY,
    "agentce.commands._verify_report: catalog_path.is_dir()": ("report-packaging",),
    "agentce.commands._verify_report: claim_path.is_file()": ("report-claim",),
    "agentce.commands._verify_report: claim_path.read_bytes()": ("report-claim",),
    "agentce.commands._verify_report: claim_path.stat()": ("report-claim",),
    "agentce.commands._verify_report: deviations_path.is_file()": ("report-packaging",),
    "agentce.commands._verify_report: deviations_path.read_bytes()": _HASHED_ONLY,
    "agentce.commands._verify_report: domain_path.is_file()": ("report-packaging",),
    "agentce.commands._verify_report: domain_path.read_bytes()": _HASHED_ONLY,
    "agentce.commands._verify_report: embedded_path.is_file()": ("report-trust-root",),
    "agentce.commands._verify_report: manifest_path.is_file()": ("report-manifest",),
    "agentce.commands._verify_report: manifest_path.read_bytes()": ("report-manifest",),
    "agentce.commands._verify_report: os.listdir(report_dir)": ("report-claim",),
    "agentce.commands._verify_report: packaging_path.is_file()": ("report-packaging",),
    "agentce.commands._verify_report: packaging_path.read_bytes()": (
        "report-packaging",
    ),
    "agentce.commands._verify_report: produced.is_file()": "disposition: the assess re-run's own "
    "output in a scratch directory this command created",
    "agentce.commands._verify_report: produced.read_bytes()": "disposition: the assess re-run's "
    "own output, compared byte for byte with the shipped file",
    "agentce.commands._verify_report: profile_path.is_file()": ("report-packaging",),
    "agentce.commands._verify_report: profile_path.read_bytes()": _HASHED_ONLY,
    "agentce.commands._verify_report: shipped.is_file()": ("report-manifest",),
    "agentce.commands._verify_report: shipped.read_bytes()": "disposition: compared byte for byte "
    "with the re-run's output; never parsed",
    "agentce.commands._verify_report: signing.parse_untrusted_json(v.payload)": (
        "report-statement",
    ),
    "agentce.commands.cmd_verify: os.path.exists(release)": ("target-argument",),
    "agentce.ingest.ingest: json.loads(line)": ("bundle-streams",),
    "agentce.ingest.ingest: path.read_bytes()": ("bundle-streams",),
    "agentce.schema.evidence_schema: json.loads(text)": _PACKAGED,
    "agentce.schema.evidence_schema: resources.files('agentce.data')"
    ".joinpath('agentce-evidence.schema.json').read_text()": _PACKAGED,
    "agentce.signing.digest_tree: folder.is_dir()": ("catalog-tree",),
    "agentce.signing.digest_tree: os.listdir(folder)": ("catalog-tree",),
    "agentce.signing.digest_tree: path.is_file()": ("catalog-tree",),
    "agentce.signing.digest_tree: path.read_bytes()": ("catalog-tree",),
    "agentce.signing.digest_tree: root.rglob('*')": ("catalog-tree",),
    "agentce.signing.load_trust_root: json.loads(path.read_text('utf-8'))": (
        "report-trust-root",
        "signer-trust-root",
    ),
    "agentce.signing.load_trust_root: path.read_text('utf-8')": (
        "report-trust-root",
        "signer-trust-root",
    ),
    "agentce.signing.parse_untrusted_json: json.loads(raw.decode('utf-8'))": (
        "catalog-sig-file",
        "release-file",
        "bundle-manifest",
    ),
    "agentce.signing.statement_subject_digest: parse_untrusted_json(payload)": (
        "catalog-statement",
        "release-statement",
    ),
    "agentce.signing.vendored_trust: json.loads(vendored_trust_path().read_text('utf-8'))": (
        _PACKAGED
    ),
    "agentce.signing.vendored_trust: vendored_trust_path().read_text('utf-8')": _PACKAGED,
    "agentce.signing.verify_catalog_directory: parse_untrusted_json(sig_path.read_bytes())": (
        "catalog-sig-file",
    ),
    "agentce.signing.verify_catalog_directory: sig_path.is_file()": (
        "catalog-sig-file",
    ),
    "agentce.signing.verify_catalog_directory: sig_path.read_bytes()": (
        "catalog-sig-file",
    ),
}


#: The read calls that touch the filesystem (READ_CALLS without the parsers): a flow point one of these
#: reads for has a file or folder that can be unreadable.
FILESYSTEM_CALLS = READ_CALLS - {"loads", "load", "safe_load", "parse_untrusted_json"}


def unreadable_problems(
    points: tuple[FlowPoint, ...] = FLOW_POINTS,
    exempt: dict[str, str] = UNREADABLE_EXEMPT,
) -> list[str]:
    """A flow point with neither an unreadable row nor an exemption, an exemption with no reason or
    that also lists the class, or an exempt point a filesystem read site in INVENTORY_READS names."""
    classes = {point.id: point.problem_classes for point in points}
    problems = [
        f"flow point {pid} has no unreadable row and no exemption"
        for pid, cls in classes.items()
        if "unreadable" not in cls and pid not in exempt
    ]
    for pid, reason in sorted(exempt.items()):
        if not str(reason).strip():
            problems.append(f"exempt flow point {pid} gives no reason")
        if "unreadable" in classes.get(pid, ()):
            problems.append(f"exempt flow point {pid} also lists the unreadable class")
    for entry, rows in sorted(INVENTORY_READS.items()):
        if isinstance(rows, str):
            continue
        call = ast.parse(entry.split(": ", 1)[1], mode="eval")
        names = {
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
            for node in ast.walk(call)
            if isinstance(node, ast.Call)
        }
        if names & FILESYSTEM_CALLS:
            problems += [
                f"exempt flow point {row} is read from the filesystem: {entry}"
                for row in rows
                if row in exempt
            ]
    return problems


def _seed_unlisted_surface(sources: dict[str, str]) -> dict[str, str]:
    """A copy of the sources with one new `verify` flag and one new reader that `cmd_verify` calls
    through a helper: what a later change could add without a census row."""
    seeded = dict(sources)
    commands = ast.parse(sources["agentce.commands.__init__"])
    for node in commands.body:
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_verify":
            node.body.insert(0, ast.parse("_census_seeded_reader(Path('.'))").body[0])
    commands.body.append(
        ast.parse(
            "def _census_seeded_reader(path):\n    return path.read_bytes()"
        ).body[0]
    )
    seeded["agentce.commands.__init__"] = ast.unparse(commands)
    cli = ast.parse(sources["agentce.cli"])
    for node in cli.body:
        if isinstance(node, ast.FunctionDef) and node.name == "build_parser":
            at = next(
                i
                for i, stmt in enumerate(node.body)
                if "'verify'" in ast.unparse(stmt) and "add_parser" in ast.unparse(stmt)
            )
            node.body.insert(
                at + 1, ast.parse("p.add_argument('--census-seeded')").body[0]
            )
    seeded["agentce.cli"] = ast.unparse(cli)
    return seeded


def self_test() -> int:
    failures: list[str] = []
    doc: Any = {"b": [1, {"c": "x"}], "a": None}
    paths = [pointer(p) for p, _ in walk(doc)]
    if paths != ["/a", "/b", "/b/0", "/b/1", "/b/1/c"]:
        failures.append(f"walk order: {paths}")
    names = [n for n, _, _ in node_mutations(doc)]
    if (
        "/b/1/c=int" not in names
        or "/b/1/c=string" in names
        or "/a-deleted" not in names
    ):
        failures.append(f"node_mutations: {names}")
    if replaced(doc, ("b", 1, "c"), 7)["b"][1]["c"] != 7 or doc["b"][1]["c"] != "x":
        failures.append("replaced must copy, not mutate")
    if _inject(b'{"a": 1}', "NaN") != b'{"census-extra": NaN, "a": 1}':
        failures.append("_inject object")
    if _inject(b"[1]", "1.0") != b"[1.0, 1]":
        failures.append("_inject array")
    byte_names = [n for n, _, _ in byte_mutations(b'{"a": 1}')]
    if "duplicate-key" not in byte_names or "number-NaN" not in byte_names:
        failures.append(f"byte_mutations: {byte_names}")
    sample = {
        "algorithm": "ed25519",
        "not_before": "2026-01-01T00:00:00Z",
        "not_after": "2026-01-01T00:10:00Z",
    }
    field_rows = [
        "certificate:" + n.replace("/", ".")
        for n, cls, _ in certificate_variants(sample)
        if cls == "certificate-field"
    ]
    if sorted(field_rows) != sorted(CERTIFICATE_FIELD_EXPECTED):
        failures.append(
            f"certificate-field rows without an expected outcome, or the reverse: {field_rows}"
        )
    covered = {point.id: set(point.problem_classes) for point in FLOW_POINTS}
    if len(covered) != len(FLOW_POINTS):
        failures.append("duplicate flow point id")
    sources = load_sources()
    failures += inventory_problems(sources)
    seeded = inventory_problems(_seed_unlisted_surface(sources))
    for expected in (
        "unlisted flag: --census-seeded",
        "unlisted read: agentce.commands._census_seeded_reader: path.read_bytes()",
    ):
        if expected not in seeded:
            failures.append(f"seeded surface not caught: {expected} (got {seeded})")
    failures += unreadable_problems()
    seeded_points = tuple(
        replace(
            point,
            problem_classes=tuple(
                c for c in point.problem_classes if c != "unreadable"
            ),
        )
        if point.id == "bundle-streams"
        else point
        for point in FLOW_POINTS
    )
    seeded_exempt = {**UNREADABLE_EXEMPT, "bundle-streams": "seeded: reads no file"}
    caught = any(
        "bundle-streams" in problem
        for problem in unreadable_problems(seeded_points, seeded_exempt)
    )
    if not caught:
        failures.append("seeded exempt reader not caught: bundle-streams")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("every file and directory reader carries an unreadable row")
    print("seeded exempt reader caught: bundle-streams")
    print(
        "SELF-TEST OK: walker, node/byte mutations, census ids, surface inventory "
        "(a seeded flag and reader are caught), certificate-field rows carry an expected outcome"
    )
    return 0


def coverage_problems(mutations: list[Mutation]) -> list[str]:
    """Every census row and problem class must generate at least one mutation, and every mutation
    must name a census row and one of its classes -- the census drives the check, not the reverse."""
    problems = []
    seen = Counter((m.flow, m.problem_class) for m in mutations)
    for point in FLOW_POINTS:
        for cls in point.problem_classes:
            if not seen[(point.id, cls)]:
                problems.append(
                    f"census row {point.id} class {cls} generated no mutation"
                )
    classes = {point.id: point.problem_classes for point in FLOW_POINTS}
    for flow, cls in seen:
        if cls not in classes.get(flow, ()):
            problems.append(f"mutation class {flow}/{cls} is not in the census")
    unreadable = {m.name for m in mutations if m.problem_class == "unreadable"}
    problems += [
        f"unreadable row {name} has no expected key, or the reverse"
        for name in sorted(unreadable ^ UNREADABLE_EXPECTED.keys())
    ]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_flow_census", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    for point in FLOW_POINTS:
        print(
            f"{point.id}: {point.reads}\n    classes: {', '.join(point.problem_classes)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
