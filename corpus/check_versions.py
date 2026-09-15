"""Verify the corpus against its version pin (SPEC §11.7).

``corpus/VERSIONS.md`` pins the SHA-256 of the ``corpus-manifest.json`` the deterministic generator
produces. This module regenerates (or reads) a corpus and checks that its manifest digest matches the
pin, so a drifted generator, catalog, or variant definition is caught before it is relied on. It backs
the phase-1 eval's P1.10 check:

    python -m corpus.check_versions --corpus <dir>

exits 0 with ``VERSIONS OK`` iff ``VERSIONS.md`` validates (it pins exactly one well-formed digest) and
that digest equals the generated corpus manifest's digest; otherwise it prints why and exits 1.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

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
    actual = str(manifest.get("digest", ""))
    if actual != pinned:
        return (
            False,
            f"corpus manifest digest {actual} does not match the VERSIONS.md pin {pinned}",
        )
    return True, f"VERSIONS OK ({pinned}, {len(manifest.get('projects', []))} projects)"


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
