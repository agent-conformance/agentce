"""Message-key catalogue for report rendering (SPEC §9.3, §8.4).

All human-readable text in ``report.md`` and ``report.html`` comes from message keys, read from the
vendored, language-neutral catalogue (``agentce/data/i18n/``, sourced from ``spec/i18n/``) that also
backs :mod:`agentce.error_catalogue`'s CLI strings, so a translation changes only the report — never
``assertions.json``, the manifest digests, or the claim. v1 ships the ``en`` catalogue complete; a
partial ``de`` catalogue demonstrates the translation mechanism (any key it omits falls back to
``en``). The report language is recorded in the manifest as ``run.report_language`` and has no effect
on the machine-readable outputs.
"""

from __future__ import annotations

import functools

from . import i18n_format

DEFAULT_LANGUAGE = "en"

#: Which of the catalogue's keys this module renders. Their English text lives in the vendored
#: catalogue, never hardcoded in this module.
_REPORT_KEY_PREFIXES = ("report.", "verdict.", "next.", "outcome.")


@functools.cache
def _full_catalog(language: str) -> dict[str, str]:
    return i18n_format.load_catalog(language)


def _report_keys(language: str) -> dict[str, str]:
    return {
        key: value
        for key, value in _full_catalog(language).items()
        if key.startswith(_REPORT_KEY_PREFIXES)
    }


def available_languages() -> list[str]:
    from . import bundled

    languages = {"en"}
    for path in bundled.i18n_dir().glob("messages.*.json"):
        languages.add(path.stem.removeprefix("messages."))
    return sorted(languages)


def catalogue(language: str = DEFAULT_LANGUAGE) -> dict[str, str]:
    """Return the report-string catalogue for ``language``, backed by ``en`` for any missing key."""
    merged = _report_keys("en")
    merged.update(_report_keys(language))
    return merged
