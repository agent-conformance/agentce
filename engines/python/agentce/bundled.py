"""Where the engine finds the data it ships (SPEC §13.4 AX-1).

The base and overlay catalogs and the quickstart project are vendored under ``agentce/data/`` and
resolved through :mod:`importlib.resources` against this package, so an installed wheel resolves them
from wherever it is installed and a source checkout resolves the same bytes. Nothing here walks up
from ``__file__`` to a repository root. ``tests/test_bundled_data.py`` holds each vendored tree
byte-identical to its ``spec/`` or ``corpus/`` original.
"""

from __future__ import annotations

import atexit
import functools
from contextlib import ExitStack
from importlib import resources
from pathlib import Path


@functools.cache
def _data_root() -> Path:
    """The ``agentce.data`` package directory as a real path (extracted once if the package is zipped)."""
    stack = ExitStack()
    atexit.register(stack.close)
    return stack.enter_context(resources.as_file(resources.files("agentce.data")))


def catalogs_dir() -> Path:
    """The vendored catalogs, laid out as ``base/<catalog>/`` and ``overlays/<catalog>/``."""
    return _data_root() / "catalogs"


def quickstart_dir() -> Path:
    """The vendored quickstart project: an evidence bundle, an applicability profile, and a domain."""
    return _data_root() / "corpus" / "quickstart"
