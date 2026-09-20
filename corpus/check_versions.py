"""Verify the corpus against its version pin (SPEC §11.7).

``corpus/VERSIONS.md`` pins the SHA-256 of the ``corpus-manifest.json`` the deterministic generator
produces. That digest covers each project's evidence bundle and its authored files: the ground truth
(``expected/outcomes.json``), the applicability profile, the domain binding, and the deviation
register. This module regenerates (or reads) a corpus, recomputes the authored-file digests from the
files on disk, and checks that the manifest agrees with the disk and its digest matches the pin, so a
drifted generator, catalog, variant definition, ground truth, or profile is caught before it is relied
on. It backs the phase-1 eval's P1.10 check:

    python -m corpus.check_versions --corpus <dir>

exits 0 with ``VERSIONS OK`` iff ``VERSIONS.md`` validates (it pins exactly one well-formed digest),
every authored file on disk matches the digest the manifest records for it, and the manifest's digest
is both the recomputed one and the pinned one; otherwise it prints why and exits 1.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from corpus.generator.generate import AUTHORED_FILES, authored_digests, corpus_digest

#: The committed version pin (SPEC §11.7); it lives beside this module in the corpus tree.
VERSIONS_MD = Path(__file__).resolve().parent / "VERSIONS.md"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def pinned_digest(versions_md: Path = VERSIONS_MD) -> str:
    """Return the single manifest digest pinned in ``VERSIONS.md``; raise ValueError if it does not
    validate (missing, or not exactly one well-formed digest)."""
    if not versions_md.is_file():
        raise ValueError(f"{versions_md} is missing")
    digests = _DIGEST_RE.findall(versions_md.read_text(encoding="utf-8"))
    if len(digests) != 1:
        raise ValueError(
            f"{versions_md} must pin exactly one sha256 manifest digest, found {len(digests)}"
        )
    return digests[0]


def _materialise(corpus_dir: Path) -> Path:
    if (corpus_dir / "corpus-manifest.json").is_file():
        return corpus_dir
    if (corpus_dir / "generator" / "generate.py").is_file():
        from corpus.generator.generate import build_corpus

        out = Path(tempfile.mkdtemp(prefix="corpus-cv-"))
        build_corpus(out, "v1")
        return out
    raise ValueError(f"{corpus_dir} has neither a corpus-manifest.json nor a generator")


def verify_versions(
    corpus_dir: Path, versions_md: Path = VERSIONS_MD
) -> tuple[bool, str]:
    """Return ``(ok, message)`` comparing the corpus manifest digest with the ``VERSIONS.md`` pin."""
    try:
        pinned = pinned_digest(versions_md)
    except ValueError as exc:
        return False, str(exc)
    try:
        corpus_root = _materialise(corpus_dir)
    except ValueError as exc:
        return False, str(exc)
    manifest = json.loads(
        (corpus_root / "corpus-manifest.json").read_text(encoding="utf-8")
    )
    projects = manifest.get("projects", [])
    for project in projects:
        problem = _authored_drift(corpus_root, project)
        if problem:
            return False, problem
    actual = str(manifest.get("digest", ""))
    recomputed = corpus_digest(projects)
    if recomputed != actual:
        return (
            False,
            f"corpus manifest digest {actual} is not the digest of its own projects {recomputed}",
        )
    if actual != pinned:
        return (
            False,
            f"corpus manifest digest {actual} does not match the VERSIONS.md pin {pinned}",
        )
    return True, f"VERSIONS OK ({pinned}, {len(projects)} projects)"


def _authored_drift(corpus_root: Path, project: dict[str, object]) -> str | None:
    """Describe how a project's authored files on disk differ from the digests its manifest entry
    records, or return None when they agree."""
    project_id = str(project.get("id", ""))
    proj_dir = corpus_root / "projects" / project_id
    missing = [key for key in AUTHORED_FILES if key not in project]
    if missing:
        return f"{project_id}: the corpus manifest does not pin {', '.join(missing)}"
    try:
        on_disk = authored_digests(proj_dir)
    except OSError as exc:
        return f"{project_id}: an authored file cannot be read ({exc})"
    for key, rel in AUTHORED_FILES.items():
        if on_disk[key] != project[key]:
            return (
                f"{project_id}: {rel} is {on_disk[key]} on disk, "
                f"but the corpus manifest pins {project[key]}"
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus.check_versions",
        description="Verify a corpus against its VERSIONS.md pin (SPEC §11.7).",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        help="the corpus directory (generated or source tree)",
    )
    args = parser.parse_args(argv)
    ok, message = verify_versions(Path(args.corpus))
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
