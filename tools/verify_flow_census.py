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
import base64
import copy
import json
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_SIGNATURE_NAME = "catalog.sig.json"


@dataclass(frozen=True)
class FlowPoint:
    id: str
    reads: str
    problem_classes: tuple[str, ...]


#: The census. Each row is one place an engine reads untrusted input during `verify --catalog` or
#: `verify --release`; its problem classes are the ways that input can be wrong. Every mutation below
#: names exactly one row, and the self-test fails if a row generates none.
FLOW_POINTS = (
    FlowPoint(
        "catalog-sig-file",
        "catalog.sig.json bytes -> JSON (verify_catalog_directory)",
        ("bytes", "number-token", "nesting", "duplicate-key", "not-a-file"),
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
        ("content", "file-name-order", "link", "empty-directory"),
    ),
    FlowPoint(
        "release-file",
        "a single-file --release envelope's bytes -> JSON",
        ("bytes", "number-token", "nesting", "duplicate-key", "not-a-file"),
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
        ("json-type", "missing-field", "base64"),
    ),
    FlowPoint(
        "manifest-file",
        "release-manifest.json bytes -> JSON -> canonical form (manifest digest)",
        ("bytes", "number-token", "nesting", "duplicate-key", "not-a-file"),
    ),
    FlowPoint(
        "manifest-fields",
        "release-manifest.json's artifacts[] entries: name, digest",
        ("json-type", "missing-field"),
    ),
    FlowPoint(
        "artifact-path",
        "an artifact name -> a file inside the release directory",
        ("path", "content"),
    ),
    FlowPoint(
        "signatures-file",
        "signatures.json bytes -> JSON",
        ("bytes", "number-token", "nesting", "duplicate-key", "not-a-file"),
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
        ),
    ),
    FlowPoint(
        "bundle-files",
        "manifest.json's files[] entries: each entry's path resolved inside the bundle "
        "(confine_to_root) and its content hashed",
        ("path", "content"),
    ),
    FlowPoint(
        "target-argument",
        "verify's own --catalog/--release/--bundle/--report value: empty, trailing-slash, '.'",
        ("empty", "trailing-slash", "dot"),
    ),
)
FLOW_IDS = frozenset(point.id for point in FLOW_POINTS)

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
    target: str  # "catalog", "release", "bundle", or "report" -- which verify flag to pass
    #: Writes this mutation's fixture into an empty per-engine directory and returns the path to pass
    #: to `verify --catalog`/`--release`/`--bundle`/`--report`. Reads only the canonical fixtures.
    build: Callable[[Path], Path]
    #: Overrides the argv value computed from `build`'s returned path (default: `str(path)`) --
    #: `target-argument` mutations build a valid fixture but pass a different spelling of its path.
    arg: Callable[[Path], str] | None = None


def _write(path: Path, data: bytes | Any) -> None:
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(data), encoding="utf-8")


def generate(canonical: Path, catalog: Path, evidence_bundle: Path) -> list[Mutation]:
    """Every mutation the census generates, from the canonical fixtures in `canonical` (built by
    `verify_parity_check.build_canonical_fixtures`), the real signed catalog at `catalog`, and the
    real signed evidence bundle at `evidence_bundle` (`corpus/quickstart/evidence`)."""
    mutations: list[Mutation] = []

    def add(
        name: str,
        flow: str,
        problem_class: str,
        build: Callable[[Path], Path],
        *,
        target: str | None = None,
        arg: Callable[[Path], str] | None = None,
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
        "not-a-file",
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

        add(f"{stem}:unreadable", "release-file", "not-a-file", unreadable)
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
        "not-a-file",
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
    if os.geteuid() != 0:  # root reads a mode-000 file anyway
        add(
            "artifact-unreadable",
            "artifact-path",
            "content",
            bundle_with(lambda dest: (dest / "artifact-a.txt").chmod(0)),
        )
        add(
            "unreadable-file",
            "catalog-tree",
            "content",
            catalog_with(lambda dest: (dest / content_file).chmod(0)),
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
        "not-a-file",
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
        "not-a-file",
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


# --- Census report and self-test. -------------------------------------------------------------------


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
    covered = {point.id: set(point.problem_classes) for point in FLOW_POINTS}
    if len(covered) != len(FLOW_POINTS):
        failures.append("duplicate flow point id")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("SELF-TEST OK: walker, node/byte mutations, census ids")
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
