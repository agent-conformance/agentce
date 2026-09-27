"""The unified sanitiser (SPEC §7 injection hardening; ``contracts/P18-18.20.md``): one category-based
core (``_neutralize``) plus three target wrappers (``sanitize_for_markdown``/``sanitize_for_terminal``/
``sanitize_for_html``), replacing the per-vector patches items 18.4 and 18.5 left behind.

Every adversarial payload is built from ``chr()``/explicit code points, never a raw literal, so no
control, bidi-override, or zero-width byte ever appears in this source file itself (matching
``spec/model/gen_vectors.py``'s own convention).
"""

from __future__ import annotations

import html
import json
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from gen_sanitize_vectors import NAMED_VECTORS
from hypothesis import given, settings
from hypothesis import strategies as st

from agentce import messages, report
from agentce.activity import DENIED_KINDS, EFFECT_CLASSES, RECORDER_CLASSES
from agentce.assertions import Assertion, EvidencePointer
from agentce.report import (
    activity_cli_lines,
    blind_spots_cli_lines,
    render_report_html,
    render_report_md,
    sanitize_for_html,
    sanitize_for_markdown,
    sanitize_for_terminal,
)

VECTORS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "spec"
    / "report"
    / "test-vectors"
    / "sanitize-vectors.json"
)

_WINDOW = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")

# --- Named adversarial payloads (chr()-built, see module docstring) --------------------------------
ESC = chr(0x1B)
DEL = chr(0x7F)
C1_SS3 = chr(0x8F)
LF, CR = chr(0x0A), chr(0x0D)
RLO, LRO, PDF_MARK = chr(0x202E), chr(0x202D), chr(0x202C)
LRI, RLI, FSI, PDI = chr(0x2066), chr(0x2067), chr(0x2068), chr(0x2069)
LRM, RLM = chr(0x200E), chr(0x200F)
ZWSP, ZWNJ, ZWJ, BOM, WJ = (
    chr(0x200B),
    chr(0x200C),
    chr(0x200D),
    chr(0xFEFF),
    chr(0x2060),
)
VARIATION_SELECTOR = chr(0xFE0F)
ASTRAL_VARIATION_SELECTOR = chr(0xE0100)
CGJ = chr(0x034F)
MONGOLIAN_FVS = chr(0x180B)
HANGUL_FILLER = chr(0x115F)
RESERVED_DICP = chr(0xFFF0)
NBSP, IDEOGRAPHIC_SPACE, EN_SPACE = chr(0x00A0), chr(0x3000), chr(0x2002)
LINE_SEP, PARA_SEP = chr(0x2028), chr(0x2029)
EMOJI = chr(0x1F600)
NONCHARACTER = chr(
    0xFDD0
)  # permanently reserved, never assigned in any Unicode version


# --- `_neutralize` core -----------------------------------------------------------------------------


def test_neutralize_replaces_control_and_del_and_c1_with_a_space() -> None:
    out = report._neutralize(f"a{ESC}b{DEL}c{C1_SS3}d", 200, "(unnamed)")
    assert ESC not in out and DEL not in out and C1_SS3 not in out
    assert out == "a b c d"


def test_neutralize_removes_the_cf_bidi_and_zero_width_range_by_category() -> None:
    payload = f"a{RLO}{LRO}{PDF_MARK}{LRI}{RLI}{FSI}{PDI}{LRM}{RLM}b{ZWSP}{ZWNJ}{ZWJ}{BOM}{WJ}c"
    out = report._neutralize(payload, 200, "(unnamed)")
    assert out == "abc"


def test_neutralize_removes_default_ignorable_codepoints_cf_does_not_cover() -> None:
    for name, ch in (
        ("variation-selector-bmp", VARIATION_SELECTOR),
        ("variation-selector-astral", ASTRAL_VARIATION_SELECTOR),
        ("cgj", CGJ),
        ("mongolian-fvs", MONGOLIAN_FVS),
        ("hangul-filler", HANGUL_FILLER),
        ("reserved-dicp", RESERVED_DICP),
    ):
        out = report._neutralize(f"a{ch}b", 200, "(unnamed)")
        assert out == "ab", f"{name}: {out!r}"
        assert unicodedata.category(ch) != "Cf", (
            f"{name} is Cf; the DICP table is redundant here"
        )


#: An independent, hand-copied transcription of the Unicode 17.0 `Default_Ignorable_Code_Point`
#: property's (first, last) ranges -- written separately from `report._DICP_RANGES` on purpose, so a
#: mutation that deletes or narrows a range in the production table (round-2 design critic finding
#: B5; the post-implementation critic's own mutation testing reproduced the same class of gap for a
#: range with no covering vector) fails *this* test even though it can't be caught by comparing the
#: table to itself. Source: unicode.org's `DerivedCoreProperties.txt` DICP block, 2026-09-27.
_EXPECTED_DICP_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def test_neutralize_drops_every_dicp_range_endpoint_independently_checked() -> None:
    for lo, hi in _EXPECTED_DICP_RANGES:
        for codepoint in {lo, hi}:
            out = report._neutralize(f"a{chr(codepoint)}b", 200, "(unnamed)")
            assert out == "ab", f"U+{codepoint:04X} (range {lo:04X}-{hi:04X}): {out!r}"


def test_has_invisible_codepoint_matches_the_independent_dicp_ranges() -> None:
    # Closes the post-implementation-critic-found gap (mutation testing: deleting or narrowing a
    # range in `report._DICP_RANGES` left every other test passing, because no fixed payload or
    # vector exercised the deleted range's own codepoints): every codepoint the independent list
    # above says is DICP must also be flagged invisible by the production predicate.
    for lo, hi in _EXPECTED_DICP_RANGES:
        for codepoint in {lo, hi}:
            assert report.has_invisible_codepoint(chr(codepoint)), f"U+{codepoint:04X}"


def test_neutralize_folds_zs_and_literal_space_runs_to_one_space() -> None:
    assert report._neutralize(f"a{NBSP}{NBSP}b", 200, "(unnamed)") == "a b"
    assert report._neutralize(f"a{IDEOGRAPHIC_SPACE}b", 200, "(unnamed)") == "a b"
    assert report._neutralize(f"a{EN_SPACE}   b", 200, "(unnamed)") == "a b"
    assert report._neutralize("a    b", 200, "(unnamed)") == "a b"


def test_neutralize_treats_line_and_paragraph_separator_as_space() -> None:
    out = report._neutralize(f"a{LINE_SEP}b{PARA_SEP}c", 200, "(unnamed)")
    assert out == "a b c"


def test_neutralize_trims_leading_and_trailing_whitespace() -> None:
    assert report._neutralize("   hello world   ", 200, "(unnamed)") == "hello world"


def test_neutralize_renders_placeholder_for_whitespace_only_input() -> None:
    assert report._neutralize("   ", 200, "(unnamed)") == "(unnamed)"
    assert report._neutralize(f"{ZWSP}{ZWNJ}", 200, "(unnamed)") == "(unnamed)"


def test_neutralize_of_empty_input_stays_empty() -> None:
    assert report._neutralize("", 200, "(unnamed)") == ""


def test_neutralize_does_not_filter_unassigned_cn_codepoints() -> None:
    assert unicodedata.category(NONCHARACTER) == "Cn"
    out = report._neutralize(f"a{NONCHARACTER}b", 200, "(unnamed)")
    assert out == f"a{NONCHARACTER}b"


def test_neutralize_treats_lone_surrogate_as_cs_and_does_not_crash() -> None:
    lone = chr(0xD800)
    assert unicodedata.category(lone) == "Cs"
    out = report._neutralize(f"a{lone}b", 200, "(unnamed)")
    assert out == "a b"


def test_neutralize_caps_by_codepoint_never_splitting_a_surrogate_pair() -> None:
    payload = "x" * 197 + EMOJI + "y" * 100
    out = report._neutralize(payload, 200, "(unnamed)")
    assert len(out) == 200
    assert out.endswith("…")
    assert EMOJI in out
    # No lone surrogate half anywhere in the (Python, per-codepoint) result.
    assert all(unicodedata.category(ch) != "Cs" for ch in out)


def test_neutralize_caps_before_html_escaping_never_cutting_an_entity() -> None:
    payload = "<" * 250
    md = sanitize_for_markdown(payload)
    assert len(md) == 200
    escaped_then_capped_would_be = html.escape(payload)[:199] + "…"
    page = sanitize_for_html(payload)
    assert page != escaped_then_capped_would_be
    assert page.count("&lt;") <= 200
    assert "&l" not in page.replace("&lt;", "")


# --- Markdown target ---------------------------------------------------------------------------


def test_sanitize_for_markdown_neutralises_backtick_and_angle_brackets() -> None:
    out = sanitize_for_markdown("a`b`<c>")
    assert "`" not in out and "<" not in out and ">" not in out


def test_sanitize_for_markdown_breaks_link_and_image_syntax() -> None:
    out = sanitize_for_markdown(
        "![Verdict: Conformant](https://attacker.example/badge.png)"
    )
    assert "[" not in out and "]" not in out


def test_sanitize_for_markdown_breaks_html_xml_entity_references() -> None:
    for payload in (
        "evil&#x202E;gnp.exe",
        "safe&zwj;x",
        "a&rlm;b",
        "a&ZeroWidthSpace;b",
    ):
        out = sanitize_for_markdown(payload)
        assert "&" not in out
        # Decoding the sanitised text (as a downstream CommonMark renderer's entity decoder would)
        # must never resurrect a bidi/zero-width codepoint that was not there before decoding.
        assert not report.has_invisible_codepoint(html.unescape(out))


def test_sanitize_for_markdown_does_not_escape_emphasis_or_pipe_or_hash() -> None:
    # Deliberately out of scope (contracts/P18-18.20.md Design/Dispositions): cosmetic only.
    out = sanitize_for_markdown("*bold* _em_ ~~strike~~ | # not-a-heading")
    assert out == "*bold* _em_ ~~strike~~ | # not-a-heading"


def test_sanitize_for_terminal_is_byte_identical_to_sanitize_for_markdown() -> None:
    for payload in (
        "plain",
        "a`b`<c>[d](e)&f",
        f"{RLO}evil{ZWSP}",
        "",
        "   ",
    ):
        assert sanitize_for_terminal(payload) == sanitize_for_markdown(payload)


# --- HTML target ---------------------------------------------------------------------------------


def test_sanitize_for_html_escapes_html_special_characters() -> None:
    import re

    out = sanitize_for_html('<img src=x onerror="alert(1)">\'&')
    assert "<" not in out and ">" not in out
    for m in re.finditer("&", out):
        assert re.match(r"&(amp|lt|gt|quot|#x27);", out[m.start() :])


def test_sanitize_for_html_strips_bidi_and_zero_width_before_escaping() -> None:
    out = sanitize_for_html(f"a{RLO}b{ZWSP}c")
    assert not report.has_invisible_codepoint(html.unescape(out))


def test_sanitize_for_html_does_not_apply_markdown_substitutions() -> None:
    # A literal backtick/bracket is inert in HTML text content; only html.escape runs.
    out = sanitize_for_html("a`b[c]d")
    assert "`" in out and "[" in out and "]" in out


# --- Placeholders --------------------------------------------------------------------------------


def test_default_placeholder_is_unnamed_field_placeholder_is_empty() -> None:
    assert sanitize_for_markdown("") == ""
    assert sanitize_for_markdown("   ") == "(unnamed)"
    assert report._sanitize_field("   ") == "(empty)"
    assert sanitize_for_html("   ") == "(unnamed)"
    assert sanitize_for_html("   ", placeholder="(empty)") == "(empty)"


# --- Fixed-seed hypothesis fuzz loop ---------------------------------------------------------------

_ADVERSARIAL_CODEPOINTS = [
    ord(c)
    for c in (
        LF,
        CR,
        ESC,
        DEL,
        C1_SS3,
        RLO,
        LRO,
        PDF_MARK,
        LRI,
        RLI,
        FSI,
        PDI,
        LRM,
        RLM,
        ZWSP,
        ZWNJ,
        ZWJ,
        BOM,
        WJ,
        VARIATION_SELECTOR,
        CGJ,
        MONGOLIAN_FVS,
        HANGUL_FILLER,
        RESERVED_DICP,
        NBSP,
        IDEOGRAPHIC_SPACE,
        LINE_SEP,
        PARA_SEP,
    )
]

_FUZZ_STRATEGY = st.text(
    alphabet=st.one_of(
        st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        st.characters(
            min_codepoint=0x20,
            max_codepoint=0x2FFFF,
            categories=("Ll", "Lu", "Lo", "Nd", "Po", "Sm", "Zs"),
        ),
        st.sampled_from([chr(cp) for cp in _ADVERSARIAL_CODEPOINTS]),
    ),
    max_size=40,
)


@settings(derandomize=True, database=None, max_examples=500, deadline=None)
@given(_FUZZ_STRATEGY)
def test_fuzz_sanitize_for_markdown_universal_properties(text: str) -> None:
    out = sanitize_for_markdown(text)
    assert "`" not in out
    assert "<" not in out and ">" not in out
    assert "[" not in out and "]" not in out
    assert "&" not in out
    assert all(unicodedata.category(ch) not in ("Cc", "Co", "Cs") for ch in out)
    assert not report.has_invisible_codepoint(out)
    assert LINE_SEP not in out and PARA_SEP not in out
    assert len(out) <= 200
    if not out:
        assert text == ""


@settings(derandomize=True, database=None, max_examples=500, deadline=None)
@given(_FUZZ_STRATEGY)
def test_fuzz_sanitize_for_html_universal_properties(text: str) -> None:
    out = sanitize_for_html(text)
    unescaped = html.unescape(out)
    assert not report.has_invisible_codepoint(unescaped)
    # No unescaped HTML metacharacter: a bare `&` may only start one of html.escape's five entities.
    import re

    for m in re.finditer("&", out):
        assert re.match(r"&(amp|lt|gt|quot|#x27);", out[m.start() :])
    assert "<" not in out and ">" not in out


@settings(derandomize=True, database=None, max_examples=500, deadline=None)
@given(_FUZZ_STRATEGY)
def test_fuzz_sanitize_for_terminal_matches_sanitize_for_markdown(text: str) -> None:
    assert sanitize_for_terminal(text) == sanitize_for_markdown(text)


# --- Render-level property (report.md/report.html/CLI can't have their container broken) -----------


def _report_fields(value: str) -> tuple[dict, dict, list[Assertion]]:
    activity = {
        "agents": [value],
        "models": [{"provider": "", "name": value, "version_or_digest": ""}],
        "tools": [{"name": value, "server": "", "protocol": ""}],
        "actions_by_effect_class": {c: 0 for c in EFFECT_CLASSES},
        "approvals_by_recorder": {c: 0 for c in RECORDER_CLASSES},
        "denied_or_blocked": {k: 0 for k in DENIED_KINDS},
        "undeclared": {"models": [value], "tools": [value]},
    }
    blind_spots = {
        "blind_spots": [
            {
                "event": value,
                "class": value,
                "ladder_rung": 1,
                "owner_key": "agent_team",
                "step_kind": "code_change",
                "supplying_adapters": [value],
                "checks_unlocked": 1,
                "unlocked_checks": [],
                "needed_by": 0,
                "needed_by_checks": [],
            }
        ],
        "no_population": [
            {
                "subject": value,
                "catalog": value,
                "control": value,
                "control_version": value,
            }
        ],
    }
    assertions = [
        Assertion(
            control=value,
            control_version="1",
            subject=value,
            outcome="conformant",
            rung=2,
            mode="automated",
            window=_WINDOW,
            population=(0, 0),
            severity="high",
            family="X",
            evidence=[
                EvidencePointer(
                    ref=value, source_class="self_report", digest="sha256:" + "a" * 64
                )
            ],
            violations=[{"focus": value}],
        )
    ]
    return activity, blind_spots, assertions


def _render_all(value: str) -> tuple[str, str, list[str]]:
    activity, blind_spots, assertions = _report_fields(value)
    cat = messages.catalogue()
    md = render_report_md(
        assertions, {"conformant": 1}, activity=activity, blind_spots=blind_spots
    )
    page = render_report_html(
        assertions, {"conformant": 1}, activity=activity, blind_spots=blind_spots
    )
    cli_lines = activity_cli_lines(activity, cat) + blind_spots_cli_lines(
        blind_spots, cat
    )
    return md, page, cli_lines


class _TagSkeleton(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tokens.append(tag)
        self.tokens.extend(name for name, _ in attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self.tokens.append(f"/{tag}")


def _tag_skeleton(page: str) -> list[str]:
    parser = _TagSkeleton()
    parser.feed(page)
    return parser.tokens


def _invisible_count(text: str) -> int:
    return sum(
        1
        for ch in text
        if unicodedata.category(ch) == "Cf" or report._is_dicp_codepoint(ord(ch))
    )


_BENIGN = "benign-name"


def _assert_container_not_broken(payload: str) -> None:
    baseline_md, baseline_html, baseline_cli = _render_all(_BENIGN)
    adversarial_md, adversarial_html, adversarial_cli = _render_all(payload)

    baseline_lines = baseline_md.splitlines()
    adversarial_lines = adversarial_md.splitlines()
    assert len(baseline_lines) == len(adversarial_lines)

    benign_span = sanitize_for_markdown(_BENIGN)
    payload_span = sanitize_for_markdown(payload)
    for base_line, adv_line in zip(baseline_lines, adversarial_lines):
        assert base_line.replace(benign_span, "") == adv_line.replace(payload_span, "")

    assert _tag_skeleton(baseline_html) == _tag_skeleton(adversarial_html)
    assert len(baseline_cli) == len(adversarial_cli)

    baseline_invisible = _invisible_count(html.unescape(baseline_md))
    adversarial_invisible = _invisible_count(html.unescape(adversarial_md))
    assert adversarial_invisible <= baseline_invisible


def _is_degenerate_span(span: str) -> bool:
    # Degenerate (empty-after-neutralize) case: covered by the placeholder tests instead. A short,
    # generic span (e.g. a lone digit or punctuation mark) is skipped too: naive string-removal can
    # collide with unrelated fixed template punctuation (a ":" separator, an all-zero tally's "0"),
    # which is not itself a container-break vector.
    return not span or span in ("(unnamed)", "(empty)") or len(span) < 4


# Every one of `gen_sanitize_vectors.NAMED_VECTORS`' 34 payloads (round-1 critic finding B3: an
# earlier version of this test hand-picked 9 of the 34, missing the newline verdict-forgery payload,
# ESC/ANSI, the line/paragraph separators, the `&rlm;`/`&ZeroWidthSpace;` entity references, the
# combined multi-vector payload, and the cap-boundary emoji string). Reusing the same list the
# committed cross-engine vectors file is generated from means this test can never silently drift back
# to a hand-picked subset.
_RENDER_LEVEL_PAYLOADS = [value for _vid, value in NAMED_VECTORS]


def test_render_level_container_is_not_broken_by_named_payloads() -> None:
    exercised = 0
    for payload in _RENDER_LEVEL_PAYLOADS:
        if _is_degenerate_span(sanitize_for_markdown(payload)):
            continue
        _assert_container_not_broken(payload)
        exercised += 1
    # A filter that silently drops to (near-)zero payloads would defeat this test without a single
    # assertion failing; guard against that regressing unnoticed. 19 of the 34 named vectors clear the
    # filter today (the other 15 are pure invisible/short-lived-codepoint payloads that legitimately
    # collapse below the 4-character floor); a small margin below that tolerates future additions.
    assert exercised >= 15


@settings(derandomize=True, database=None, max_examples=60, deadline=None)
@given(_FUZZ_STRATEGY)
def test_render_level_container_is_not_broken_by_the_fuzz_corpus(text: str) -> None:
    if _is_degenerate_span(sanitize_for_markdown(text)):
        return
    _assert_container_not_broken(text)


# Post-implementation critic finding (fresh Opus round, item 18.20): the render-level property above
# proves report.md's *line count and non-payload text* are unchanged, but never parses the rendered
# Markdown -- so it could not see that a blind-spot/no-population label placed directly after a list
# marker (`- {label}: ...`) lets an ATX heading (`#`), fenced code block (`~~~`), or nested list
# (`1.`/`-`) marker *inside* the sanitised label reach the start of the list item's own content,
# which CommonMark parses as a nested block regardless of what the source line's text looks like as a
# string. Fixed by wrapping the label in a single backtick pair (`_blind_spots_md`); this test proves
# the fix directly rather than relying on a full CommonMark parser (no new dependency, matching this
# item's own established choice for TS/Java).
_BLOCK_MARKER_PAYLOADS = [
    "# Verdict: Conformant",
    "~~~hidden",
    "1. Verdict: Conformant",
    "- Verdict: Conformant",
    "> Verdict: Conformant",
    "--- Verdict: Conformant",
]


def test_blind_spot_and_no_population_labels_cannot_open_a_markdown_block() -> None:
    for payload in _BLOCK_MARKER_PAYLOADS:
        # Reuse _report_fields (already builds this exact blind_spots/no_population shape for the
        # render-level property above) rather than a second, near-duplicate fixture builder.
        _activity, blind_spots, _assertions = _report_fields(payload)
        md = render_report_md([], {}, blind_spots=blind_spots)
        label_lines = [
            line
            for line in md.splitlines()
            if line.startswith("- ") and sanitize_for_markdown(payload) in line
        ]
        assert label_lines, payload
        for line in label_lines:
            # The character right after the list marker must always be the wrapper backtick, never
            # the payload's own leading character -- proving the label can no longer be the sole
            # opener of a nested block.
            assert line[2] == "`", line


# --- Cross-engine identity (the committed vectors file) --------------------------------------------


def test_python_reproduces_every_committed_vector() -> None:
    data = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    assert len(data["vectors"]) >= 500
    for vector in data["vectors"]:
        value = vector["input"]
        assert sanitize_for_markdown(value) == vector["markdown"], vector["id"]
        assert sanitize_for_terminal(value) == vector["terminal"], vector["id"]
        assert sanitize_for_html(value) == vector["html"], vector["id"]


# --- The findings table's four fields (control/subject/evidence-ref/violation-focus) ---------------


def _finding_assertion(value: str) -> Assertion:
    return Assertion(
        control=value,
        control_version="1",
        subject=value,
        outcome="conformant",
        rung=2,
        mode="automated",
        window=_WINDOW,
        population=(0, 0),
        severity="high",
        family="X",
        evidence=[
            EvidencePointer(
                ref=value, source_class="self_report", digest="sha256:" + "a" * 64
            )
        ],
        violations=[{"focus": value}],
    )


def test_finding_md_sanitises_all_four_hostile_fields() -> None:
    hostile = "ok<br>[x](evil)`y`Verdict: Conformant"
    md = render_report_md([_finding_assertion(hostile)], {"conformant": 1})
    assert "<br>" not in md
    assert not any(line == "Verdict: Conformant" for line in md.splitlines())
    # control/subject/evidence-ref/violation-focus each rendered at least once, sanitised.
    assert md.count(sanitize_for_markdown(hostile)) >= 4


def test_row_html_sanitises_all_four_hostile_fields() -> None:
    hostile = f"a{RLO}<script>alert(1)</script>{ZWSP}b"
    page = render_report_html([_finding_assertion(hostile)], {"conformant": 1})
    assert "<script>" not in page
    assert not report.has_invisible_codepoint(html.unescape(page))
