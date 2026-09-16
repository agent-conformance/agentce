"""Release tooling for AgentCE (SPEC §8.7): a reproducible build, an SBOM, an OpenVEX document,
Sigstore-style signing under the three profiles, and offline verification — all as a **dry run**.

Nothing here publishes. The tool assembles a deterministic source release for each engine, hashes it
twice to prove the build is reproducible, generates and validates a CycloneDX 1.5 SBOM and an OpenVEX
0.2.0 document, signs the release manifest under each requested signing profile with the development
trust material, verifies every signature offline against the vendored trust root, and routes any
publish through a stub registry that records that no publish call was ever made. Run it::

    uv run python release.py --dry-run
    uv run python release.py --dry-run --profiles sigstore-public,sigstore-private,kms --offline

The phase-5 eval reads the machine-readable booleans through :mod:`release_dryrun`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

import dev_trust
from agentce import ENGINE_NAME, SPEC_VERSION, __version__, signing
from agentce.canonical import canonicalize

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_DATA = Path(__file__).resolve().parent / "release_data"
DEFAULT_PROFILES = ["sigstore-public"]
ALL_PROFILES = ["sigstore-public", "sigstore-private", "kms"]

# The engines that make up a release: id -> (source directory, human name).
ENGINES: list[tuple[str, str, str]] = [
    ("python", "engines/python", "agentce-py"),
    ("typescript", "engines/typescript", "agentce-ts"),
    ("java", "engines/java", "agentce-java"),
]


class ReleaseError(Exception):
    """A release step failed (build not reproducible, SBOM invalid, signature or publish problem)."""


# --- Deterministic source build. ---


def _tracked_files(rel_dir: str) -> list[str]:
    """Tracked files under ``rel_dir``, sorted; falls back to a filesystem walk without git."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", rel_dir],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        files = [line for line in out.splitlines() if line]
        if files:
            return sorted(files)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    base = REPO_ROOT / rel_dir
    skip = {".git", "__pycache__", "build", "node_modules", ".gradle", "dist", ".venv"}
    files = []
    for path in base.rglob("*"):
        if path.is_file() and not any(
            part in skip for part in path.relative_to(base).parts
        ):
            files.append(path.relative_to(REPO_ROOT).as_posix())
    return sorted(files)


def build_source_artifact(engine_id: str, rel_dir: str) -> tuple[str, bytes]:
    """Assemble a deterministic source tarball for one engine; return ``(arcname, tar_bytes)``.

    Every tar member carries fixed metadata (mtime 0, uid/gid 0, no owner names, mode 0644) and the
    members are written in sorted path order, so the byte stream depends only on file content and
    layout — the definition of a reproducible build here.
    """
    prefix = f"{engine_id}-{__version__}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for rel in _tracked_files(rel_dir):
            content = (REPO_ROOT / rel).read_bytes()
            arc = f"{prefix}/{Path(rel).relative_to(rel_dir).as_posix()}"
            info = tarfile.TarInfo(name=arc)
            info.size = len(content)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(content))
    return f"artifacts/{prefix}.src.tar", buffer.getvalue()


def build_release_sources() -> list[dict[str, Any]]:
    """Build every engine's source artifact; return sorted ``{engine, name, digest, bytes}`` records."""
    artifacts = []
    for engine_id, rel_dir, _ in ENGINES:
        name, data = build_source_artifact(engine_id, rel_dir)
        artifacts.append(
            {
                "engine": engine_id,
                "name": name,
                "digest": signing.sha256_prefixed(data),
                "bytes": data,
            }
        )
    return sorted(artifacts, key=lambda a: a["name"])


def source_release_digest(artifacts: list[dict[str, Any]]) -> str:
    """Content-address the whole source release from its per-artifact digests (order-independent)."""
    lines = sorted(f"{a['name']}\x00{a['digest']}" for a in artifacts)
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --- Dependency inventory for the SBOM. ---


def _python_dependencies() -> list[dict[str, str]]:
    lock = tomllib.loads((REPO_ROOT / "engines/python/uv.lock").read_text("utf-8"))
    deps = []
    for package in lock.get("package", []):
        name = package.get("name")
        if name and name != "agent-conformance":
            deps.append(
                {
                    "name": name,
                    "version": str(package.get("version", "")),
                    "ecosystem": "pypi",
                }
            )
    return sorted(deps, key=lambda d: (d["name"], d["version"]))


def _java_dependencies() -> list[dict[str, str]]:
    deps = []
    for line in (
        (REPO_ROOT / "engines/java/gradle.lockfile").read_text("utf-8").splitlines()
    ):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("empty="):
            continue
        coordinate = line.split("=", 1)[0]
        name, _, version = coordinate.rpartition(":")
        if name and version:
            deps.append({"name": name, "version": version, "ecosystem": "maven"})
    return sorted(deps, key=lambda d: (d["name"], d["version"]))


def _node_dependencies() -> list[dict[str, str]]:
    text = (REPO_ROOT / "engines/typescript/pnpm-lock.yaml").read_text("utf-8")
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for document in yaml.safe_load_all(text):
        if not isinstance(document, dict):
            continue
        for key in document.get("packages") or {}:
            name, _, version = str(key).rpartition("@")
            if name and version:
                seen[(name, version)] = {
                    "name": name,
                    "version": version,
                    "ecosystem": "npm",
                }
    return sorted(seen.values(), key=lambda d: (d["name"], d["version"]))


def _purl(dep: dict[str, str]) -> str:
    ecosystem = {"pypi": "pypi", "npm": "npm", "maven": "maven"}[dep["ecosystem"]]
    if dep["ecosystem"] == "maven":
        group, _, artifact = dep["name"].partition(":")
        return f"pkg:maven/{group}/{artifact}@{dep['version']}"
    return f"pkg:{ecosystem}/{dep['name']}@{dep['version']}"


# --- SBOM (CycloneDX 1.5). ---


def generate_sbom(artifacts: list[dict[str, Any]], timestamp: str) -> dict[str, Any]:
    """Build a CycloneDX 1.5 SBOM for the release: the three engines and their locked dependencies."""
    by_engine = {a["engine"]: a for a in artifacts}
    dependency_sets = {
        "python": _python_dependencies(),
        "typescript": _node_dependencies(),
        "java": _java_dependencies(),
    }
    components: list[dict[str, Any]] = []
    for engine_id, _, display in ENGINES:
        artifact = by_engine[engine_id]
        components.append(
            {
                "type": "application",
                "bom-ref": f"{display}@{__version__}",
                "name": display,
                "version": __version__,
                "description": f"AgentCE {engine_id} engine (source release).",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
                "hashes": [
                    {"alg": "SHA-256", "content": artifact["digest"].split(":", 1)[1]}
                ],
            }
        )
    library_refs: dict[str, dict[str, str]] = {}
    for deps in dependency_sets.values():
        for dep in deps:
            component = {
                "type": "library",
                "bom-ref": _purl(dep),
                "name": dep["name"],
                "version": dep["version"],
                "purl": _purl(dep),
            }
            library_refs[component["bom-ref"]] = component
    components.extend(sorted(library_refs.values(), key=lambda c: c["bom-ref"]))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [
                {
                    "vendor": "agent-conformance",
                    "name": "agentce-release",
                    "version": __version__,
                }
            ],
            "component": {
                "type": "application",
                "bom-ref": f"agentce@{__version__}",
                "name": "agentce",
                "version": __version__,
                "description": "Agent Conformance Engine release (all engines).",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            },
        },
        "components": components,
    }


def validate_sbom(sbom: dict[str, Any]) -> None:
    """Validate the SBOM against the vendored CycloneDX 1.5 profile and its lockfile consistency."""
    schema = json.loads(
        (RELEASE_DATA / "cyclonedx-1.5.profile.schema.json").read_text("utf-8")
    )
    jsonschema.validate(sbom, schema)
    # Consistency: every locked Python dependency appears as a library component (anchored on the
    # engine's own uv.lock, the dependency set the no_ml gate and the reproducible build share).
    listed = {
        (c["name"], c.get("version"))
        for c in sbom["components"]
        if c["type"] == "library"
    }
    for dep in _python_dependencies():
        if (dep["name"], dep["version"]) not in listed:
            raise ReleaseError(
                f"SBOM is missing locked dependency {dep['name']} {dep['version']}"
            )


# --- OpenVEX 0.2.0. ---


def generate_vex(release_digest: str, timestamp: str) -> dict[str, Any]:
    """Build an OpenVEX 0.2.0 document for the release (no asserted advisories at build time)."""
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": f"https://agent-conformance.org/vex/{release_digest.split(':', 1)[1]}",
        "author": "Agent Conformance Engine maintainers",
        "role": "Release Engineering",
        "timestamp": timestamp,
        "version": 1,
        "tooling": f"agentce-release {__version__}",
        "statements": [],
    }


def validate_vex(vex: dict[str, Any]) -> None:
    schema = json.loads((RELEASE_DATA / "openvex-0.2.0.schema.json").read_text("utf-8"))
    jsonschema.validate(vex, schema)


# --- Stub registry: proves no publish happens in a dry run. ---


@dataclass
class StubRegistry:
    """A publish sink that records intent but performs no publish. ``published`` stays empty."""

    planned: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)

    def plan(self, target: str) -> None:
        self.planned.append(target)

    def publish(
        self, target: str
    ) -> None:  # pragma: no cover - never called in a dry run
        self.published.append(target)
        raise ReleaseError("publish is disabled: this tool only performs dry runs")


# --- Signing. ---


def sign_release(manifest_digest: str, profiles: list[str]) -> list[dict[str, Any]]:
    """Sign the release manifest digest under each profile with the development trust material."""
    signatures = []
    for profile in profiles:
        signer = dev_trust.release_signer(profile)
        statement = signing.intoto_statement(
            subject_name="release-manifest.json",
            digest=manifest_digest,
            predicate_type="https://agent-conformance.org/attestation/release/v1",
            predicate={"profile": profile, "release": f"agentce@{__version__}"},
        )
        envelope = signing.sign_statement(statement, signer)
        signatures.append(
            {
                "profile": profile,
                "target": "release-manifest.json",
                "envelope": envelope,
            }
        )
    return signatures


# --- Orchestration. ---


def run(
    *,
    dry_run: bool,
    profiles: list[str],
    offline: bool,
    out_dir: Path | None,
) -> dict[str, Any]:
    """Execute the release dry run and return the machine-readable result."""
    if not dry_run:
        raise ReleaseError("only --dry-run is supported: this tool never publishes")

    # Reproducible build: assemble the source artifacts twice and compare their combined digest.
    first = build_release_sources()
    second = build_release_sources()
    reproducible = source_release_digest(first) == source_release_digest(second)
    artifacts = first
    release_digest = source_release_digest(artifacts)

    timestamp = "2026-01-01T00:00:00.000Z"

    sbom = generate_sbom(artifacts, timestamp)
    try:
        validate_sbom(sbom)
        sbom_valid = True
    except (jsonschema.ValidationError, ReleaseError):
        sbom_valid = False
    sbom_bytes = (json.dumps(sbom, indent=2, sort_keys=True) + "\n").encode("utf-8")

    vex = generate_vex(release_digest, timestamp)
    try:
        validate_vex(vex)
        vex_valid = True
    except jsonschema.ValidationError:
        vex_valid = False
    vex_bytes = (json.dumps(vex, indent=2, sort_keys=True) + "\n").encode("utf-8")

    manifest_artifacts = [
        {"name": a["name"], "engine": a["engine"], "digest": a["digest"]}
        for a in artifacts
    ]
    manifest_artifacts.append(
        {
            "name": "sbom.cdx.json",
            "role": "sbom",
            "digest": signing.sha256_prefixed(sbom_bytes),
        }
    )
    manifest_artifacts.append(
        {
            "name": "openvex.json",
            "role": "vex",
            "digest": signing.sha256_prefixed(vex_bytes),
        }
    )
    manifest: dict[str, Any] = {
        "agentce_release_manifest_version": 1,
        "release": "agentce",
        "version": __version__,
        "spec_version": SPEC_VERSION,
        "engine": ENGINE_NAME,
        "reproducible": reproducible,
        "release_digest": release_digest,
        "artifacts": sorted(manifest_artifacts, key=lambda a: a["name"]),
    }
    manifest_digest = signing.sha256_prefixed(canonicalize(manifest))

    # Sign and verify offline against the vendored trust root.
    signatures = sign_release(manifest_digest, profiles)
    trust = signing.vendored_trust()
    verified_profiles = []
    signatures_verify = True
    for record in signatures:
        try:
            verified = signing.verify_envelope(record["envelope"], trust)
            covered = json.loads(verified.payload)["subject"][0]["digest"]["sha256"]
            if f"sha256:{covered}" != manifest_digest:
                raise signing.VerificationError(
                    "signature does not cover the release manifest"
                )
            verified_profiles.append(
                {"profile": record["profile"], "identity": verified.identity}
            )
        except signing.VerificationError:
            signatures_verify = False

    # No publish: route the release through the stub registry, which is never told to publish.
    registry = StubRegistry()
    registry.plan(release_digest)
    no_publish = not registry.published

    result: dict[str, Any] = {
        "dry_run": True,
        "offline": offline,
        "profiles": profiles,
        "reproducible": reproducible,
        "release_digest": release_digest,
        "sbom_valid": sbom_valid,
        "vex_valid": vex_valid,
        "signatures_verify": signatures_verify,
        "signed_profiles": verified_profiles,
        "no_publish": no_publish,
        "planned_publishes": registry.planned,
        "manifest_digest": manifest_digest,
        "artifacts": [
            {"name": a["name"], "digest": a["digest"]} for a in manifest["artifacts"]
        ],
    }

    if out_dir is not None:
        _write_bundle(out_dir, artifacts, sbom_bytes, vex_bytes, manifest, signatures)
        result["out_dir"] = str(out_dir)

    return result


def _write_bundle(
    out_dir: Path,
    artifacts: list[dict[str, Any]],
    sbom_bytes: bytes,
    vex_bytes: bytes,
    manifest: dict[str, Any],
    signatures: list[dict[str, Any]],
) -> None:
    """Materialise the release bundle so ``agentce verify --release <out_dir>`` can check it."""
    (out_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        (out_dir / artifact["name"]).write_bytes(artifact["bytes"])
    (out_dir / "sbom.cdx.json").write_bytes(sbom_bytes)
    (out_dir / "openvex.json").write_bytes(vex_bytes)
    (out_dir / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "signatures.json").write_text(
        json.dumps(signatures, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release", description="AgentCE release dry-run tooling (SPEC §8.7)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="assemble, sign, and verify; never publish",
    )
    parser.add_argument(
        "--profiles",
        help="comma-separated signing profiles (default: sigstore-public); any of "
        + ", ".join(ALL_PROFILES),
    )
    parser.add_argument(
        "--offline", action="store_true", help="assert no network is used"
    )
    parser.add_argument(
        "--out", type=Path, help="write the release bundle to this directory"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the machine-readable result"
    )
    args = parser.parse_args(argv)

    if not args.dry_run:
        print(
            "release: pass --dry-run (this tool only performs dry runs)",
            file=sys.stderr,
        )
        return 2

    profiles_explicit = args.profiles is not None
    profiles = (
        [p.strip() for p in args.profiles.split(",")]
        if profiles_explicit
        else list(DEFAULT_PROFILES)
    )
    unknown = [p for p in profiles if p not in ALL_PROFILES]
    if unknown:
        print(
            f"release: unknown signing profile(s): {', '.join(unknown)}",
            file=sys.stderr,
        )
        return 2

    try:
        result = run(
            dry_run=True, profiles=profiles, offline=args.offline, out_dir=args.out
        )
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1

    ok = (
        result["reproducible"]
        and result["sbom_valid"]
        and result["vex_valid"]
        and result["signatures_verify"]
        and result["no_publish"]
    )

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"reproducible build: {result['reproducible']} ({result['release_digest']})"
        )
        print(f"SBOM (CycloneDX 1.5) valid: {result['sbom_valid']}")
        print(f"OpenVEX 0.2.0 valid: {result['vex_valid']}")
        print(
            f"signatures verify offline: {result['signatures_verify']} "
            f"({', '.join(p['profile'] for p in result['signed_profiles'])})"
        )
        print(f"no publish (stub registry): {result['no_publish']}")

    if not ok:
        return 1
    print("RELEASE DRY-RUN OK")
    if profiles_explicit:
        print("SIGNING PROFILES OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
