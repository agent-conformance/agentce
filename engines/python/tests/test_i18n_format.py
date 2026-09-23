"""The ICU MessageFormat subset the catalogue uses: plain interpolation and plural/select."""

from __future__ import annotations

from agentce import i18n_format, verdict
from agentce.messages import catalogue


def test_plain_interpolation() -> None:
    assert i18n_format.format_message("See {dir} for detail.", dir="out") == (
        "See out for detail."
    )


def test_template_without_placeholders_is_returned_unchanged() -> None:
    assert i18n_format.format_message("no placeholders here") == "no placeholders here"


def test_plural_one_category() -> None:
    template = "{n, plural, one {# item} other {# items}}"
    assert i18n_format.format_message(template, n=1) == "1 item"


def test_plural_other_category() -> None:
    template = "{n, plural, one {# item} other {# items}}"
    assert i18n_format.format_message(template, n=0) == "0 items"
    assert i18n_format.format_message(template, n=5) == "5 items"


def test_plural_exact_value_category_wins_over_the_cldr_rule() -> None:
    # ICU lets a template pin an exact value (`=0 {...}`) ahead of the general "one"/"other" rule.
    template = "{n, plural, =0 {none} one {# item} other {# items}}"
    assert i18n_format.format_message(template, n=0) == "none"
    assert i18n_format.format_message(template, n=1) == "1 item"


def test_select() -> None:
    template = "{state, select, on {enabled} off {disabled} other {unknown}}"
    assert i18n_format.format_message(template, state="on") == "enabled"
    assert i18n_format.format_message(template, state="off") == "disabled"
    assert i18n_format.format_message(template, state="missing") == "unknown"


def test_report_gaps_more_is_a_real_icu_plural_construct() -> None:
    template = catalogue("en")["report.gaps_more"]
    assert "plural" in template
    assert i18n_format.format_message(template, n=1) == "+1 more gap"
    assert i18n_format.format_message(template, n=3) == "+3 more gaps"


def test_gap_text_renders_the_plural_more_suffix() -> None:
    cat = catalogue("en")
    gap = {"outcome": "insufficient_evidence", "controls": ["DAT-01"], "more": 1}
    assert verdict.gap_text(gap, cat).endswith("(+1 more gap)")
    gap["more"] = 4
    assert verdict.gap_text(gap, cat).endswith("(+4 more gaps)")


def test_load_catalog_unknown_language_is_empty() -> None:
    assert i18n_format.load_catalog("xx-not-a-real-language") == {}


def test_load_catalog_en_is_never_empty() -> None:
    assert len(i18n_format.load_catalog("en")) > 50
