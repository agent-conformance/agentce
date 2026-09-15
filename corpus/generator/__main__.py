"""Package entrypoint: ``python -m corpus.generator --out <dir>`` (SPEC §11.2, eval interface)."""

from __future__ import annotations

from .generate import main

if __name__ == "__main__":
    raise SystemExit(main())
