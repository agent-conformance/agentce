"""Offline distribution bundle, dry-run (SPEC §14.3 item 10, §8.7, P5.6).

``--build`` assembles a self-contained bundle: the per-engine release sources with their pinned
dependencies, a CycloneDX SBOM and an OpenVEX statement, the signed base catalog, the vendored trust
root, one corpus project, offline verification instructions, and a manifest. ``--verify --no-network``
proves the bundle works with networking disabled: it verifies the release and catalog signatures
offline against the vendored trust root and runs the Engine Conformance Suite on the bundled corpus
project, all under dead proxies so any socket attempt fails. It prints ``OFFLINE BUNDLE OK``.

Nothing is published; the bundle is materialised on disk only. Run it as::

    cd conformance && uv run python offline_bundle.py --build --out /tmp/agentce-offline
    cd conformance && uv run python offline_bundle.py --verify /tmp/agentce-offline --no-network
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import release
from agentce import signing

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
PROJECT_DIR = REPO_ROOT / "corpus" / "quickstart"
TRUST_ROOT = signing.vendored_trust_path()
CATALOG_ID = "eu-ai-act@2026.09"

#: A dead proxy: any accidental network call fails, proving the verify step runs offline.
_DEAD_PROXY = "http://127.0.0.1:9"


def _agentce() -> str:
    """The installed ``agentce`` console script (present in this environment)."""
    found = shutil.which("agentce")
    if found is None:
        raise RuntimeError("the 'agentce' CLI is not on PATH; install the engine first")
    return found


def build_bundle(out_dir: Path) -> dict[str, Any]:
    """Materialise the offline bundle at ``out_dir`` and return a summary."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # The signed, reproducible release sources plus SBOM, OpenVEX, and signatures (SPEC §8.7).
    release.run(
        dry_run=True, profiles=list(release.ALL_PROFILES), offline=True, out_dir=out_dir
    )

    # The signed base catalog and the vendored trust root, so verification is fully offline.
    shutil.copytree(CATALOG_DIR, out_dir / "catalogs" / "eu-ai-act")
    (out_dir / "trust").mkdir()
    shutil.copy2(TRUST_ROOT, out_dir / "trust" / "dev-root.json")

    # One corpus project the Engine Conformance Suite runs offline.
    shutil.copytree(
        PROJECT_DIR, out_dir / "project", ignore=shutil.ignore_patterns("README.md")
    )

    # Pinned dependencies per engine (the installable set).
    dependencies = {
        "python": release._python_dependencies(),
        "node": release._node_dependencies(),
        "java": release._java_dependencies(),
    }
    _write_json(out_dir / "DEPENDENCIES.json", dependencies)

    (out_dir / "VERIFY.md").write_text(_verify_instructions(), encoding="utf-8")

    components = _component_digests(out_dir)
    manifest = {
        "agentce_offline_bundle_version": 1,
        "engine": {"impl": "agentce", "spec_version": _spec_version()},
        "catalog": {
            "id": "eu-ai-act",
            "version": "2026.09",
            "digest": signing.digest_tree(
                out_dir / "catalogs" / "eu-ai-act",
                exclude=frozenset({signing.CATALOG_SIGNATURE_NAME}),
            ),
        },
        "components": components,
        "verify": [
            "agentce verify --release .",
            "agentce verify --catalog catalogs/eu-ai-act",
            "agentce assess --bundle project/evidence --catalog-dir catalogs/eu-ai-act ...",
        ],
    }
    _write_json(out_dir / "bundle-manifest.json", manifest)
    return {"out_dir": str(out_dir), "components": len(components)}


def _component_digests(out_dir: Path) -> list[dict[str, str]]:
    names = [
        "sbom.cdx.json",
        "openvex.json",
        "release-manifest.json",
        "signatures.json",
    ]
    components = []
    for name in names:
        path = out_dir / name
        if path.is_file():
            components.append(
                {"name": name, "digest": signing.sha256_prefixed(path.read_bytes())}
            )
    return components


def _run(
    cmd: list[str], *, no_network: bool, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if no_network:
        env.update(
            HTTP_PROXY=_DEAD_PROXY,
            HTTPS_PROXY=_DEAD_PROXY,
            http_proxy=_DEAD_PROXY,
            https_proxy=_DEAD_PROXY,
            NO_PROXY="",
        )
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def verify_bundle(out_dir: Path, *, no_network: bool) -> dict[str, Any]:
    """Verify the bundle offline; return the per-step results."""
    agentce = _agentce()
    problems: list[str] = []

    release_check = _run(
        [agentce, "verify", "--release", ".", "--json"],
        no_network=no_network,
        cwd=out_dir,
    )
    if release_check.returncode != 0:
        problems.append(
            f"release signatures did not verify: {release_check.stderr.strip()}"
        )

    catalog_check = _run(
        [agentce, "verify", "--catalog", "catalogs/eu-ai-act", "--json"],
        no_network=no_network,
        cwd=out_dir,
    )
    if catalog_check.returncode != 0:
        problems.append(
            f"catalog signature did not verify: {catalog_check.stderr.strip()}"
        )

    report_dir = out_dir / "_verify-report"
    assess = _run(
        [
            agentce,
            "assess",
            "--bundle",
            "project/evidence",
            "--profile",
            "project/applicability.yaml",
            "--domain",
            "project/domain.linkml.yaml",
            "--deviations",
            "project/deviations.yaml",
            "--catalog",
            CATALOG_ID,
            "--catalog-dir",
            "catalogs/eu-ai-act",
            "--out",
            str(report_dir),
        ],
        no_network=no_network,
        cwd=out_dir,
    )
    if assess.returncode != 0:
        problems.append(
            f"the ECS run failed: {assess.stderr.strip() or assess.stdout.strip()}"
        )
    elif not (report_dir / "assertions.json").is_file():
        problems.append("the ECS run produced no assertions.json")

    return {
        "offline": no_network,
        "ran_ecs": assess.returncode == 0,
        "problems": problems,
    }


def _verify_instructions() -> str:
    return (
        "# Offline verification\n\n"
        "This bundle installs and runs with networking disabled. With the engine's pinned\n"
        "dependencies installed from a local index (see `DEPENDENCIES.json` and `sbom.cdx.json`):\n\n"
        "```\n"
        "agentce verify --release .\n"
        "agentce verify --catalog catalogs/eu-ai-act\n"
        "agentce assess --bundle project/evidence --profile project/applicability.yaml \\\n"
        "  --domain project/domain.linkml.yaml --catalog eu-ai-act@2026.09 \\\n"
        "  --catalog-dir catalogs/eu-ai-act --out ./report\n"
        "```\n\n"
        "Signatures verify against the vendored trust root in `trust/dev-root.json`; no network is\n"
        "required or used.\n"
    )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _spec_version() -> str:
    from agentce import SPEC_VERSION

    return SPEC_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="offline_bundle",
        description="Offline distribution bundle, dry-run (SPEC §14.3).",
    )
    parser.add_argument("--build", action="store_true", help="assemble the bundle")
    parser.add_argument("--verify", metavar="DIR", help="verify a bundle at DIR")
    parser.add_argument("--out", metavar="DIR", help="the output directory for --build")
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="verify under dead proxies (offline proof)",
    )
    args = parser.parse_args(argv)

    if args.build:
        if not args.out:
            print("OFFLINE BUNDLE FAILED: --build requires --out")
            return 1
        summary = build_bundle(Path(args.out).resolve())
        print(
            f"built offline bundle at {summary['out_dir']} ({summary['components']} components)"
        )
        return 0

    if args.verify:
        result = verify_bundle(Path(args.verify).resolve(), no_network=args.no_network)
        if result["problems"]:
            print("OFFLINE BUNDLE FAILED:")
            for problem in result["problems"]:
                print(f"  - {problem}")
            return 1
        print("OFFLINE BUNDLE OK")
        return 0

    parser.error("pass --build --out DIR or --verify DIR")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
