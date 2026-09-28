package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The report renderers' canonical machine outputs are byte-identical to the reference (SPEC §9).
 * {@code report-golden.json} holds the reference engine's {@code render_*} output for the OVS-03
 * failed scenario; the SARIF engine name is the only engine-specific field.
 *
 * <p>The canonical machine outputs -- OSCAL, SARIF, the evidence pack, and assertions.json -- are
 * byte-identical to the reference and are asserted below. The human-readable renderers (report.md,
 * report.html) still diverge from the reference: the reference groups findings by control severity
 * with a verdict banner, per-finding evidence/violations/remediation, and a provenance block (SPEC
 * §9.3), none of which this engine's renderer emits yet. Bringing this engine to that parity is
 * engine-parity work owned by a later item (the TypeScript engine tracks the same gap explicitly in
 * {@code report.test.ts}), so md/html are not compared here.
 */
class ReportTest {

    private static List<Assertions.Assertion> ovsFailedAssertions() throws IOException {
        Catalog catalog = Catalog.load(Fixtures.BASE);
        DomainBinding domain = DomainBinding.load(Fixtures.BASE.resolve("test/domain.yaml"));
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\":[{\"id\":\"spiffe://corp/agents/a\",\"role\":\"both\"}]}"));
        List<JsonNode> events = Fixtures.readJsonl(Fixtures.BASE.resolve("test/OVS-03/failed.jsonl"));
        return Assess.assessSubjects(events, profile, List.of(catalog), domain);
    }

    @Test
    void reportRenderersMatchReference() throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("report-golden.json"));
        List<Assertions.Assertion> assertions = ovsFailedAssertions();
        Map<String, Integer> counts = Assertions.aggregate(assertions);

        assertEquals(Canonical.canonicalString(golden.get("oscal")), Canonical.canonicalString(Report.renderOscal(assertions)));
        assertEquals(
                Canonical.canonicalString(golden.get("sarif")),
                Canonical.canonicalString(Report.renderSarif(assertions)).replace("agentce-java", "agentce-py"));
        assertEquals(
                Canonical.canonicalString(golden.get("pack")),
                Canonical.canonicalString(Report.renderEvidencePack("spiffe://corp/agents/a", assertions, null)));
        ArrayNode myAssertions = Json.nodes().arrayNode();
        for (Assertions.Assertion a : assertions) {
            myAssertions.add(a.toJson());
        }
        assertEquals(Canonical.canonicalString(golden.get("assertions")), Canonical.canonicalString(myAssertions));
    }

    /** Every verdict state, the gap cap, and a control assessed for two subjects render without error.
     * The golden's {@code md}/{@code html} fields exercise the reference's verdict-banner rendering
     * (SPEC §9.3), which this engine does not implement yet (see the class doc); this keeps the
     * renderer under coverage for each scenario rather than asserting byte-identity it cannot meet. */
    @Test
    void verdictCasesRenderWithoutError() throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("report-golden.json"));
        assertEquals(3, golden.get("verdict_cases").size());
        for (JsonNode verdictCase : golden.get("verdict_cases")) {
            List<Assertions.Assertion> assertions = new java.util.ArrayList<>();
            for (JsonNode row : verdictCase.get("assertions")) {
                Assertions.Assertion a = new Assertions.Assertion();
                a.control = row.get(0).textValue();
                a.controlVersion = "2026.09";
                a.subject = row.get(1).textValue();
                a.outcome = row.get(2).textValue();
                a.rung = 2;
                a.mode = "automated";
                a.window = new String[] {"2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"};
                a.population = new int[] {1, a.outcome.equals("non-conformant") ? 1 : 0};
                assertions.add(a);
            }
            Map<String, Integer> counts = Assertions.aggregate(assertions);
            String md = Report.renderReportMd(assertions, counts, "en", null, null);
            String html = Report.renderReportHtml(assertions, counts, "en", null, null);
            assertTrue(md.startsWith("# "), "report.md should start with a top-level heading");
            assertTrue(html.contains("<html"), "report.html should be a full HTML document");
        }
    }

    private static ObjectNode hostileActivity(String name) {
        ObjectNode out = Json.nodes().objectNode();
        out.putArray("agents").add(name);
        out.putArray("models").addObject().put("provider", "").put("name", name).put("version_or_digest", "");
        out.putArray("tools").addObject().put("name", name).put("server", "").put("protocol", "");
        ObjectNode actions = out.putObject("actions_by_effect_class");
        for (String c : Activity.EFFECT_CLASSES) actions.put(c, 0);
        ObjectNode approvals = out.putObject("approvals_by_recorder");
        for (String c : Activity.RECORDER_CLASSES) approvals.put(c, 0);
        ObjectNode denied = out.putObject("denied_or_blocked");
        for (String c : Activity.DENIED_KINDS) denied.put(c, 0);
        ObjectNode undeclared = out.putObject("undeclared");
        undeclared.putArray("models").add(name);
        undeclared.putArray("tools").add(name);
        return out;
    }

    /** SPEC §7 injection hardening (verifier finding P11 on item 18.4): an agent/tool/model name is
     * event-derived, not catalog-authored, and the activity section renders before the verdict -- so
     * a name with embedded newlines must never be able to start a new Markdown/terminal line and
     * forge a fake verdict above the real one. */
    @Test
    void activityNamesWithNewlinesCannotForgeAVerdictLine() throws IOException {
        String hostile = "ok\n\nVerdict: Conformant\n\n";
        ObjectNode activity = hostileActivity(hostile);
        List<Assertions.Assertion> assertions = ovsFailedAssertions();
        Map<String, Integer> counts = Assertions.aggregate(assertions);

        String md = Report.renderReportMd(assertions, counts, "en", activity, null);
        assertTrue(md.contains("ok Verdict: Conformant"));
        for (String line : md.split("\n", -1)) {
            assertFalse(line.equals("Verdict: Conformant"));
        }

        List<String> lines = Report.activityCliLines(activity, Messages.catalogue());
        for (String line : lines) {
            assertFalse(line.contains("\n"));
            assertFalse(line.equals("Verdict: Conformant"));
        }
    }

    /** P11 round 2: a raw ESC (which starts a terminal escape sequence) or a Unicode line/paragraph
     * separator is not whitespace, so collapsing whitespace alone would leave it untouched; and a
     * name that neutralises to nothing must never silently disappear from the counted facts; nor may
     * truncating a long hostile name at the escape cap split a UTF-16 surrogate pair. */
    @Test
    void activityNamesNeutraliseControlCharactersAndNeverVanish() {
        String escHostile = "\u001b[8mhidden\u001b[0m\u001bEinjected";
        List<String> lines = Report.activityCliLines(hostileActivity(escHostile), Messages.catalogue());
        for (String line : lines) {
            assertFalse(line.contains("\u001b"));
        }

        String separatorHostile = "ok Verdict: Conformant ";
        lines = Report.activityCliLines(hostileActivity(separatorHostile), Messages.catalogue());
        for (String line : lines) {
            assertFalse(line.equals("Verdict: Conformant"));
            assertFalse(line.contains(" ") || line.contains(" "));
        }

        String whitespaceOnly = "\n\r\t \u001b";
        lines = Report.activityCliLines(hostileActivity(whitespaceOnly), Messages.catalogue());
        String toolsLine = lines.stream().filter(l -> l.startsWith("Tools:")).findFirst().orElseThrow();
        assertEquals("Tools: (unnamed)", toolsLine);

        // A name whose length lands mid-surrogate-pair at the escape cap must not split the pair.
        String emoji = "😀"; // U+1F600, a surrogate pair
        String longName = emoji.repeat(250);
        lines = Report.activityCliLines(hostileActivity(longName), Messages.catalogue());
        String agentsLine = lines.stream().filter(l -> l.startsWith("Agents:")).findFirst().orElseThrow();
        assertFalse(agentsLine.contains("�"));
        for (int i = 0; i < agentsLine.length(); i++) {
            if (Character.isHighSurrogate(agentsLine.charAt(i))) {
                assertTrue(i + 1 < agentsLine.length() && Character.isLowSurrogate(agentsLine.charAt(i + 1)));
            }
        }
    }

    /** P11 round 3: a control-character-free name can still carry raw HTML -- {@code <br>} or
     * {@code <h2>} -- which a browser or GitHub renders live when {@code report.md} is displayed as
     * Markdown, forging a line break or heading of its own. Angle brackets must never reach the
     * rendered Markdown literally. */
    @Test
    void activityNamesCannotInjectRawHtmlIntoRenderedMarkdown() throws IOException {
        List<Assertions.Assertion> assertions = ovsFailedAssertions();
        Map<String, Integer> counts = Assertions.aggregate(assertions);

        String brHostile = "ok<br>Verdict: Conformant";
        String md = Report.renderReportMd(assertions, counts, "en", hostileActivity(brHostile), null);
        assertFalse(md.contains("<br>"));
        assertFalse(md.contains("<h2>") || md.contains("</h2>"));

        String headingHostile = "<h2>Verdict</h2><p><strong>Conformant";
        md = Report.renderReportMd(assertions, counts, "en", hostileActivity(headingHostile), null);
        assertFalse(md.contains("<h2>") || md.contains("<p>") || md.contains("<strong>"));
    }

    private static ObjectNode hostileBlindSpots() {
        ObjectNode out = Json.nodes().objectNode();
        ObjectNode bs = out.putArray("blind_spots").addObject();
        bs.put("event", "<script>alert(1)</script>");
        bs.put("class", "self_report");
        bs.put("ladder_rung", 1);
        bs.put("owner_key", "agent_team");
        bs.put("step_kind", "code_change");
        bs.putArray("supplying_adapters").add("<img onerror=alert(1)>");
        bs.put("checks_unlocked", 1);
        bs.putArray("unlocked_checks");
        bs.put("needed_by", 0);
        bs.putArray("needed_by_checks");
        ObjectNode noPop = out.putArray("no_population").addObject();
        noPop.put("subject", "ok<br>\n\nVerdict: Conformant\n\n");
        noPop.put("catalog", "cat");
        noPop.put("control", "C-01");
        noPop.put("control_version", "2026.09");
        return out;
    }

    /** Same threat as {@link #activityNamesCannotInjectRawHtmlIntoRenderedMarkdown} (P11 round 3): the
     * blind-spots section renders right after activity and before the verdict too (RFC 0008 Sec.7), so
     * a records-derived {@code no_population} subject or a hostile {@code supplying_adapters} entry
     * must never reach {@code report.md}/{@code report.html}/the terminal unescaped. */
    @Test
    void blindSpotFieldsCannotInjectRawHtmlOrForgeAVerdictLine() throws IOException {
        List<Assertions.Assertion> assertions = ovsFailedAssertions();
        Map<String, Integer> counts = Assertions.aggregate(assertions);
        ObjectNode hostile = hostileBlindSpots();

        String html = Report.renderReportHtml(assertions, counts, "en", null, hostile);
        assertFalse(html.contains("<script>alert"));
        assertFalse(html.contains("<img onerror"));

        String md = Report.renderReportMd(assertions, counts, "en", null, hostile);
        assertFalse(md.contains("<br>"));
        assertFalse(md.contains("\n\nVerdict: Conformant\n\n"));

        String cli = String.join("\n", Report.blindSpotsCliLines(hostile));
        assertFalse(cli.contains("<br>"));
        assertFalse(cli.contains("\n\nVerdict: Conformant\n\n"));
    }

    @Test
    void writeReportEmitsEveryArtifactAndAManifest(@TempDir Path outDir) throws IOException {
        List<Assertions.Assertion> assertions = ovsFailedAssertions();
        ObjectNode manifest = Report.writeReport(
                outDir, assertions, "sha256:abc", List.of("base/eu-ai-act@1"), "ecs", List.of("conformance", "p1"),
                List.of(), "en", List.of(), null, null);

        for (String name : List.of(
                "assertions.json", "activity.json", "blind-spots.json", "report.md", "report.html", "oscal-ar.json",
                "results.sarif", "manifest.json")) {
            assertTrue(Files.exists(outDir.resolve(name)), name + " should exist");
        }
        assertEquals("agentce-java", manifest.get("engine").get("impl").textValue());
        assertTrue(manifest.get("outputs").has("assertions.json"));
        String onDisk = Files.readString(outDir.resolve("manifest.json"));
        assertTrue(onDisk.startsWith("{\n  \"agentce_manifest_version\": 1,"), "manifest is Python-style pretty JSON");
    }

    // --- buildManifest's real catalog digest (item 18.22: never sha256:000...0) -- the same shared
    // fixture tree CatalogTest's digestTree tests and TypeScript's report.test.ts read. ---

    private static final Path DIGEST_FIXTURE =
            TestPaths.repoRoot().resolve("spec/model/test-vectors/digest-tree");

    private static String digestExpected() throws IOException {
        return Files.readString(TestPaths.repoRoot().resolve("spec/model/test-vectors/digest-tree.expected")).strip();
    }

    @Test
    void buildManifestCarriesTheCatalogsRealContentDigestNeverTheAllZeroConstant() throws IOException {
        Catalog fixture = Catalog.load(DIGEST_FIXTURE);
        ObjectNode manifest = Report.buildManifest(
                "sha256:abc", List.of("fixture@1"), Map.of(), "test", List.of(), List.of(), "en", List.of(fixture));
        JsonNode ref = manifest.get("inputs").get("catalogs").get(0);
        assertEquals(digestExpected(), ref.get("digest").textValue());
        assertNotEquals("sha256:" + "0".repeat(64), ref.get("digest").textValue());
    }

    @Test
    void buildManifestKeepsTheAllZeroDigestForALabelWithNoMatchingCatalogObject() {
        ObjectNode manifest = Report.buildManifest(
                "sha256:abc", List.of("unknown@1"), Map.of(), "test", List.of(), List.of(), "en", List.of());
        JsonNode ref = manifest.get("inputs").get("catalogs").get(0);
        assertEquals("sha256:" + "0".repeat(64), ref.get("digest").textValue());
    }

    // --- Verdict.gapText's report.gaps_more plural fix (item 18.22): Java previously hardcoded
    // "+{n} more" instead of the message catalogue's real ICU-plural string. ---

    @Test
    void gapTextRendersReportGapsMoresIcuPluralCorrectlyAtTheSingularPluralBoundary() {
        Map<String, String> cat = Messages.catalogue("en");
        Verdict.Gap singular = new Verdict.Gap("partial", List.of("A"), 1);
        Verdict.Gap plural = new Verdict.Gap("partial", List.of("A"), 14);
        assertTrue(Verdict.gapText(singular, cat).endsWith("(+1 more gap)"));
        assertTrue(Verdict.gapText(plural, cat).endsWith("(+14 more gaps)"));
    }

    // --- The unified sanitiser (SPEC §7 injection hardening; contracts/P18-18.20.md): named
    // adversarial payloads built from explicit code points, never a raw literal, so no control,
    // bidi-override, or zero-width character ever appears in this source file itself (matching
    // engines/python/tests/test_sanitize.py's own convention). Mirrors that file one-for-one, ported
    // idiomatically; `neutralize` itself is private (as in the TypeScript port), so these tests go
    // through `sanitizeForMarkdown`/`sanitizeForHtml`'s default cap (200) and name placeholder
    // ("(unnamed)"), which match `_neutralize`'s own defaults exactly. ---

    private static String cp(int codepoint) {
        return new String(Character.toChars(codepoint));
    }

    private static final String ESC = cp(0x1B);
    private static final String DEL = cp(0x7F);
    private static final String C1_SS3 = cp(0x8F);
    private static final String RLO = cp(0x202E);
    private static final String LRO = cp(0x202D);
    private static final String PDF_MARK = cp(0x202C);
    private static final String LRI = cp(0x2066);
    private static final String RLI = cp(0x2067);
    private static final String FSI = cp(0x2068);
    private static final String PDI = cp(0x2069);
    private static final String LRM = cp(0x200E);
    private static final String RLM = cp(0x200F);
    private static final String ZWSP = cp(0x200B);
    private static final String ZWNJ = cp(0x200C);
    private static final String ZWJ = cp(0x200D);
    private static final String BOM = cp(0xFEFF);
    private static final String WJ = cp(0x2060);
    private static final String VARIATION_SELECTOR = cp(0xFE0F);
    private static final String ASTRAL_VARIATION_SELECTOR = cp(0xE0100);
    private static final String CGJ = cp(0x034F);
    private static final String MONGOLIAN_FVS = cp(0x180B);
    private static final String HANGUL_FILLER = cp(0x115F);
    private static final String RESERVED_DICP = cp(0xFFF0);
    private static final String NBSP = cp(0x00A0);
    private static final String IDEOGRAPHIC_SPACE = cp(0x3000);
    private static final String EN_SPACE = cp(0x2002);
    private static final String LINE_SEP = cp(0x2028);
    private static final String PARA_SEP = cp(0x2029);
    private static final String EMOJI = cp(0x1F600);
    private static final String NONCHARACTER = cp(0xFDD0); // permanently reserved, never assigned

    private static String unescapeHtmlEntities(String s) {
        return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
                .replace("&#x27;", "'").replace("&amp;", "&");
    }

    // --- `_neutralize` core (via `sanitizeForMarkdown`'s default cap/placeholder) ---

    @Test
    void neutralizeReplacesControlAndDelAndC1WithASpace() {
        assertEquals("a b c d", Report.sanitizeForMarkdown("a" + ESC + "b" + DEL + "c" + C1_SS3 + "d"));
    }

    @Test
    void neutralizeRemovesTheCfBidiAndZeroWidthRangeByCategory() {
        String payload = "a" + RLO + LRO + PDF_MARK + LRI + RLI + FSI + PDI + LRM + RLM
                + "b" + ZWSP + ZWNJ + ZWJ + BOM + WJ + "c";
        assertEquals("abc", Report.sanitizeForMarkdown(payload));
    }

    @Test
    void neutralizeRemovesDefaultIgnorableCodepointsCfDoesNotCover() {
        for (String ch : new String[] {
            VARIATION_SELECTOR, ASTRAL_VARIATION_SELECTOR, CGJ, MONGOLIAN_FVS, HANGUL_FILLER, RESERVED_DICP
        }) {
            assertEquals("ab", Report.sanitizeForMarkdown("a" + ch + "b"), ch);
            assertNotEquals(Character.FORMAT, (byte) Character.getType(ch.codePointAt(0)), ch + " is Cf");
        }
    }

    // An independent, hand-copied transcription of the Unicode 17.0 Default_Ignorable_Code_Point
    // property's (first, last) ranges -- written separately from Report.java's own DICP_RANGES on
    // purpose, so a mutation that deletes or narrows a range in the production table (the
    // post-implementation critic's own mutation testing: removing a range with no covering test left
    // every other test passing) fails *this* test even though it can't be caught by comparing the
    // table to itself.
    private static final int[][] EXPECTED_DICP_RANGES = {
        {0x00AD, 0x00AD},
        {0x034F, 0x034F},
        {0x061C, 0x061C},
        {0x115F, 0x1160},
        {0x17B4, 0x17B5},
        {0x180B, 0x180F},
        {0x200B, 0x200F},
        {0x202A, 0x202E},
        {0x2060, 0x206F},
        {0x3164, 0x3164},
        {0xFE00, 0xFE0F},
        {0xFEFF, 0xFEFF},
        {0xFFA0, 0xFFA0},
        {0xFFF0, 0xFFF8},
        {0x1BCA0, 0x1BCA3},
        {0x1D173, 0x1D17A},
        {0xE0000, 0xE0FFF},
    };

    @Test
    void neutralizeDropsEveryDicpRangeEndpointIndependentlyChecked() {
        for (int[] range : EXPECTED_DICP_RANGES) {
            for (int codepoint : new int[] {range[0], range[1]}) {
                String out = Report.sanitizeForMarkdown("a" + cp(codepoint) + "b");
                assertEquals("ab", out, "U+" + Integer.toHexString(codepoint).toUpperCase());
            }
        }
    }

    @Test
    void hasInvisibleCodepointMatchesTheIndependentDicpRanges() {
        for (int[] range : EXPECTED_DICP_RANGES) {
            for (int codepoint : new int[] {range[0], range[1]}) {
                assertTrue(
                        Report.hasInvisibleCodepoint(cp(codepoint)),
                        "U+" + Integer.toHexString(codepoint).toUpperCase());
            }
        }
    }

    @Test
    void neutralizeFoldsZsAndLiteralSpaceRunsToOneSpace() {
        assertEquals("a b", Report.sanitizeForMarkdown("a" + NBSP + NBSP + "b"));
        assertEquals("a b", Report.sanitizeForMarkdown("a" + IDEOGRAPHIC_SPACE + "b"));
        assertEquals("a b", Report.sanitizeForMarkdown("a" + EN_SPACE + "   b"));
        assertEquals("a b", Report.sanitizeForMarkdown("a    b"));
    }

    @Test
    void neutralizeTreatsLineAndParagraphSeparatorAsSpace() {
        assertEquals("a b c", Report.sanitizeForMarkdown("a" + LINE_SEP + "b" + PARA_SEP + "c"));
    }

    @Test
    void neutralizeTrimsLeadingAndTrailingWhitespace() {
        assertEquals("hello world", Report.sanitizeForMarkdown("   hello world   "));
    }

    @Test
    void neutralizeRendersPlaceholderForWhitespaceOnlyInput() {
        assertEquals("(unnamed)", Report.sanitizeForMarkdown("   "));
        assertEquals("(unnamed)", Report.sanitizeForMarkdown(ZWSP + ZWNJ));
    }

    @Test
    void neutralizeOfEmptyInputStaysEmpty() {
        assertEquals("", Report.sanitizeForMarkdown(""));
    }

    @Test
    void neutralizeDoesNotFilterUnassignedCnCodepoints() {
        assertEquals(Character.UNASSIGNED, (byte) Character.getType(NONCHARACTER.codePointAt(0)));
        assertEquals("a" + NONCHARACTER + "b", Report.sanitizeForMarkdown("a" + NONCHARACTER + "b"));
    }

    @Test
    void neutralizeTreatsLoneSurrogateAsCsAndDoesNotCrash() {
        String lone = String.valueOf((char) 0xD800);
        assertEquals(Character.SURROGATE, (byte) Character.getType(0xD800));
        assertEquals("a b", Report.sanitizeForMarkdown("a" + lone + "b"));
    }

    @Test
    void neutralizeCapsByCodepointNeverSplittingASurrogatePair() {
        String payload = "x".repeat(197) + EMOJI + "y".repeat(100);
        String out = Report.sanitizeForMarkdown(payload);
        assertEquals(200, out.codePointCount(0, out.length()));
        assertTrue(out.endsWith("…"));
        assertTrue(out.contains(EMOJI));
        for (int i = 0; i < out.length(); i++) {
            if (Character.isHighSurrogate(out.charAt(i))) {
                assertTrue(i + 1 < out.length() && Character.isLowSurrogate(out.charAt(i + 1)));
            }
        }
    }

    @Test
    void neutralizeCapsBeforeHtmlEscapingNeverCuttingAnEntity() {
        String payload = "<".repeat(250);
        String md = Report.sanitizeForMarkdown(payload);
        assertEquals(200, md.codePointCount(0, md.length()));
        String page = Report.sanitizeForHtml(payload);
        assertTrue(page.replace("&lt;", "").indexOf("&l") < 0);
        long ltCount = (page.length() - page.replace("&lt;", "").length()) / "&lt;".length();
        assertTrue(ltCount <= 200);
    }

    // --- Markdown target ---

    @Test
    void sanitizeForMarkdownNeutralisesBacktickAndAngleBrackets() {
        String out = Report.sanitizeForMarkdown("a`b`<c>");
        assertFalse(out.contains("`"));
        assertFalse(out.contains("<"));
        assertFalse(out.contains(">"));
    }

    @Test
    void sanitizeForMarkdownBreaksLinkAndImageSyntax() {
        String out = Report.sanitizeForMarkdown("![Verdict: Conformant](https://attacker.example/badge.png)");
        assertFalse(out.contains("["));
        assertFalse(out.contains("]"));
    }

    @Test
    void sanitizeForMarkdownBreaksHtmlXmlEntityReferences() {
        for (String payload : new String[] {"evil&#x202E;gnp.exe", "safe&zwj;x", "a&rlm;b", "a&ZeroWidthSpace;b"}) {
            String out = Report.sanitizeForMarkdown(payload);
            assertFalse(out.contains("&"), payload);
            assertFalse(Report.hasInvisibleCodepoint(unescapeHtmlEntities(out)), payload);
        }
    }

    @Test
    void sanitizeForMarkdownDoesNotEscapeEmphasisOrPipeOrHash() {
        String out = Report.sanitizeForMarkdown("*bold* _em_ ~~strike~~ | # not-a-heading");
        assertEquals("*bold* _em_ ~~strike~~ | # not-a-heading", out);
    }

    @Test
    void sanitizeForTerminalIsByteIdenticalToSanitizeForMarkdown() {
        for (String payload : new String[] {"plain", "a`b`<c>[d](e)&f", RLO + "evil" + ZWSP, "", "   "}) {
            assertEquals(Report.sanitizeForMarkdown(payload), Report.sanitizeForTerminal(payload));
        }
    }

    // --- HTML target ---

    @Test
    void sanitizeForHtmlEscapesHtmlSpecialCharacters() {
        String out = Report.sanitizeForHtml("<img src=x onerror=\"alert(1)\">'&");
        assertFalse(out.contains("<"));
        assertFalse(out.contains(">"));
        Matcher m = Pattern.compile("&").matcher(out);
        while (m.find()) {
            assertTrue(Pattern.compile("&(amp|lt|gt|quot|#x27);").matcher(out.substring(m.start())).lookingAt());
        }
    }

    @Test
    void sanitizeForHtmlStripsBidiAndZeroWidthBeforeEscaping() {
        String out = Report.sanitizeForHtml("a" + RLO + "b" + ZWSP + "c");
        assertFalse(Report.hasInvisibleCodepoint(unescapeHtmlEntities(out)));
    }

    @Test
    void sanitizeForHtmlDoesNotApplyMarkdownSubstitutions() {
        String out = Report.sanitizeForHtml("a`b[c]d");
        assertTrue(out.contains("`"));
        assertTrue(out.contains("["));
        assertTrue(out.contains("]"));
    }

    // --- Placeholders ---

    @Test
    void defaultPlaceholderIsUnnamedFieldPlaceholderIsEmpty() {
        assertEquals("", Report.sanitizeForMarkdown(""));
        assertEquals("(unnamed)", Report.sanitizeForMarkdown("   "));
        assertEquals("(unnamed)", Report.sanitizeForHtml("   "));
        assertEquals("(empty)", Report.sanitizeForHtml("   ", "(empty)"));
    }

    // --- Fixed-seed fuzz loop (java.util.Random(42), no new dependency -- mirrors the Python
    // reference's hypothesis fuzz loop and the TypeScript port's own hand-rolled PRNG loop) ---

    private static final int[] ADVERSARIAL_CODEPOINTS = {
        0x0A, 0x0D, 0x1B, 0x7F, 0x8F, 0x202E, 0x202D, 0x202C, 0x2066, 0x2067, 0x2068, 0x2069, 0x200E,
        0x200F, 0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0xFE0F, 0x034F, 0x180B, 0x115F, 0xFFF0, 0x00A0,
        0x3000, 0x2028, 0x2029,
    };

    private static int randomFuzzCodepoint(Random rnd) {
        int choice = rnd.nextInt(3);
        if (choice == 0) {
            return 0x20 + rnd.nextInt(0x7E - 0x20 + 1);
        }
        if (choice == 1) {
            return ADVERSARIAL_CODEPOINTS[rnd.nextInt(ADVERSARIAL_CODEPOINTS.length)];
        }
        while (true) {
            int candidate = 0x20 + rnd.nextInt(0x2FFFF - 0x20);
            if (candidate >= 0xD800 && candidate <= 0xDFFF) {
                continue;
            }
            int type = Character.getType(candidate);
            if (type == Character.LOWERCASE_LETTER
                    || type == Character.UPPERCASE_LETTER
                    || type == Character.OTHER_LETTER
                    || type == Character.DECIMAL_DIGIT_NUMBER
                    || type == Character.OTHER_PUNCTUATION
                    || type == Character.MATH_SYMBOL
                    || type == Character.SPACE_SEPARATOR) {
                return candidate;
            }
        }
    }

    private static String randomFuzzString(Random rnd) {
        int len = rnd.nextInt(41);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < len; i++) {
            sb.appendCodePoint(randomFuzzCodepoint(rnd));
        }
        return sb.toString();
    }

    @Test
    void fuzzSanitizeForMarkdownUniversalProperties() {
        Random rnd = new Random(42);
        for (int i = 0; i < 500; i++) {
            String text = randomFuzzString(rnd);
            String out = Report.sanitizeForMarkdown(text);
            assertFalse(out.contains("`"));
            assertFalse(out.contains("<"));
            assertFalse(out.contains(">"));
            assertFalse(out.contains("["));
            assertFalse(out.contains("]"));
            assertFalse(out.contains("&"));
            for (int codePoint : out.codePoints().toArray()) {
                int type = Character.getType(codePoint);
                assertFalse(type == Character.CONTROL || type == Character.PRIVATE_USE || type == Character.SURROGATE);
            }
            assertFalse(Report.hasInvisibleCodepoint(out));
            assertFalse(out.contains(LINE_SEP));
            assertFalse(out.contains(PARA_SEP));
            assertTrue(out.codePointCount(0, out.length()) <= 200);
            if (out.isEmpty()) {
                assertEquals("", text);
            }
        }
    }

    @Test
    void fuzzSanitizeForHtmlUniversalProperties() {
        Random rnd = new Random(42);
        for (int i = 0; i < 500; i++) {
            String out = Report.sanitizeForHtml(randomFuzzString(rnd));
            assertFalse(Report.hasInvisibleCodepoint(unescapeHtmlEntities(out)));
            Matcher m = Pattern.compile("&").matcher(out);
            while (m.find()) {
                assertTrue(Pattern.compile("&(amp|lt|gt|quot|#x27);").matcher(out.substring(m.start())).lookingAt());
            }
            assertFalse(out.contains("<"));
            assertFalse(out.contains(">"));
        }
    }

    @Test
    void fuzzSanitizeForTerminalMatchesSanitizeForMarkdown() {
        Random rnd = new Random(42);
        for (int i = 0; i < 500; i++) {
            String text = randomFuzzString(rnd);
            assertEquals(Report.sanitizeForMarkdown(text), Report.sanitizeForTerminal(text));
        }
    }

    // --- Render-level property (report.md/report.html/CLI can't have their container broken) ---

    private static ObjectNode renderLevelBlindSpots(String value) {
        ObjectNode out = Json.nodes().objectNode();
        ObjectNode bs = out.putArray("blind_spots").addObject();
        bs.put("event", value);
        bs.put("class", value);
        bs.put("ladder_rung", 1);
        bs.put("owner_key", "agent_team");
        bs.put("step_kind", "code_change");
        bs.putArray("supplying_adapters").add(value);
        bs.put("checks_unlocked", 1);
        bs.putArray("unlocked_checks");
        bs.put("needed_by", 0);
        bs.putArray("needed_by_checks");
        ObjectNode noPop = out.putArray("no_population").addObject();
        noPop.put("subject", value);
        noPop.put("catalog", value);
        noPop.put("control", value);
        noPop.put("control_version", value);
        return out;
    }

    private static Assertions.Assertion renderLevelAssertion(String value) {
        Assertions.Assertion a = new Assertions.Assertion();
        a.control = value;
        a.controlVersion = "1";
        a.subject = value;
        a.outcome = "conformant";
        a.rung = 2;
        a.mode = "automated";
        a.window = new String[] {"2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"};
        a.population = new int[] {0, 0};
        return a;
    }

    private record RenderResult(String md, String html, List<String> cliLines) {}

    private static RenderResult renderAll(String value) {
        ObjectNode activity = hostileActivity(value);
        ObjectNode blindSpots = renderLevelBlindSpots(value);
        List<Assertions.Assertion> assertions = List.of(renderLevelAssertion(value));
        Map<String, Integer> counts = Map.of("conformant", 1);
        String md = Report.renderReportMd(assertions, counts, "en", activity, blindSpots);
        String html = Report.renderReportHtml(assertions, counts, "en", activity, blindSpots);
        List<String> cliLines = new ArrayList<>(Report.activityCliLines(activity, Messages.catalogue()));
        cliLines.addAll(Report.blindSpotsCliLines(blindSpots));
        return new RenderResult(md, html, cliLines);
    }

    private static List<String> tagSkeleton(String html) {
        List<String> tokens = new ArrayList<>();
        Matcher m = Pattern.compile("<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>").matcher(html);
        while (m.find()) {
            String tag = m.group(2);
            tokens.add(m.group(1).isEmpty() ? tag : "/" + tag);
            Matcher am = Pattern.compile("([a-zA-Z][a-zA-Z0-9-]*)\\s*=").matcher(m.group(3));
            while (am.find()) {
                tokens.add(am.group(1));
            }
        }
        return tokens;
    }

    private static long countInvisible(String text) {
        return text.codePoints()
                .filter(codePoint -> Character.getType(codePoint) == Character.FORMAT || Report.isDicpCodepoint(codePoint))
                .count();
    }

    private static final String BENIGN = "benign-name";

    private static void assertContainerNotBroken(String payload) {
        RenderResult baseline = renderAll(BENIGN);
        RenderResult adversarial = renderAll(payload);

        String[] baseLines = baseline.md().split("\n", -1);
        String[] advLines = adversarial.md().split("\n", -1);
        assertEquals(baseLines.length, advLines.length);

        String benignSpan = Report.sanitizeForMarkdown(BENIGN);
        String payloadSpan = Report.sanitizeForMarkdown(payload);
        for (int i = 0; i < baseLines.length; i++) {
            assertEquals(baseLines[i].replace(benignSpan, ""), advLines[i].replace(payloadSpan, ""));
        }

        assertEquals(tagSkeleton(baseline.html()), tagSkeleton(adversarial.html()));
        assertEquals(baseline.cliLines().size(), adversarial.cliLines().size());

        long baselineInvisible = countInvisible(unescapeHtmlEntities(baseline.md()));
        long adversarialInvisible = countInvisible(unescapeHtmlEntities(adversarial.md()));
        assertTrue(adversarialInvisible <= baselineInvisible);
    }

    private static boolean isDegenerateSpan(String span) {
        // Degenerate (empty-after-neutralize) case: covered by the placeholder tests instead. A
        // short, generic span (e.g. a lone digit or punctuation mark) is skipped too: naive
        // string-removal can collide with unrelated fixed template punctuation (a ":" separator, an
        // all-zero tally's "0"), which is not itself a container-break vector.
        return span.isEmpty() || span.equals("(unnamed)") || span.equals("(empty)") || span.length() < 4;
    }

    private static JsonNode cachedVectorsFile;

    private static JsonNode loadVectorsFile() throws IOException {
        if (cachedVectorsFile == null) {
            cachedVectorsFile = Json.parseFile(TestPaths.repoRoot().resolve("spec/report/test-vectors/sanitize-vectors.json"));
        }
        return cachedVectorsFile;
    }

    private static List<String> loadNamedVectorInputs() throws IOException {
        List<String> inputs = new ArrayList<>();
        for (JsonNode vector : loadVectorsFile().get("vectors")) {
            if (!vector.get("id").asText().startsWith("fuzz-")) {
                inputs.add(vector.get("input").asText());
            }
        }
        return inputs;
    }

    @Test
    void renderLevelContainerIsNotBrokenByNamedPayloads() throws IOException {
        // Every one of the committed vectors file's named (non-fuzz) payloads (round-1 critic
        // finding B3: an earlier version of this test hand-picked 9 of the 34, missing the newline
        // verdict-forgery payload, ESC/ANSI, the line/paragraph separators, the `&rlm;`/
        // `&ZeroWidthSpace;` entity references, the combined multi-vector payload, and the
        // cap-boundary emoji string). Loading the same generated file the cross-engine identity test
        // below uses means this can never silently drift back to a hand-picked subset.
        int exercised = 0;
        for (String payload : loadNamedVectorInputs()) {
            if (isDegenerateSpan(Report.sanitizeForMarkdown(payload))) {
                continue;
            }
            assertContainerNotBroken(payload);
            exercised++;
        }
        // A filter that silently drops to (near-)zero payloads would defeat this test without a
        // single assertion failing; guard against that regressing unnoticed. 19 of the 34 named
        // vectors clear the filter today (the other 15 are pure invisible/short-lived-codepoint
        // payloads that legitimately collapse below the 4-character floor); a small margin below that
        // tolerates future additions.
        assertTrue(exercised >= 15);
    }

    @Test
    void renderLevelContainerIsNotBrokenByTheFuzzCorpus() {
        Random rnd = new Random(42);
        for (int i = 0; i < 60; i++) {
            String text = randomFuzzString(rnd);
            if (isDegenerateSpan(Report.sanitizeForMarkdown(text))) {
                continue;
            }
            assertContainerNotBroken(text);
        }
    }

    // Post-implementation critic finding (fresh Opus round, item 18.20): the render-level property
    // above proves report.md's line count and non-payload text are unchanged, but never parses the
    // rendered Markdown -- so it could not see that a blind-spot/no-population label placed directly
    // after a list marker ("- " + label + ": ...") lets an ATX heading ("#"), fenced code block
    // ("~~~"), or nested list ("1."/"-") marker inside the sanitised label reach the start of the
    // list item's own content, which CommonMark parses as a nested block regardless of what the
    // source line's text looks like as a string. Fixed by wrapping the label in a single backtick
    // pair (blindSpotsMd); this test proves the fix directly rather than relying on a full CommonMark
    // parser (no new dependency).
    private static final String[] BLOCK_MARKER_PAYLOADS = {
        "# Verdict: Conformant",
        "~~~hidden",
        "1. Verdict: Conformant",
        "- Verdict: Conformant",
        "> Verdict: Conformant",
        "--- Verdict: Conformant",
    };

    @Test
    void blindSpotAndNoPopulationLabelsCannotOpenAMarkdownBlock() {
        for (String payload : BLOCK_MARKER_PAYLOADS) {
            ObjectNode blindSpots = renderLevelBlindSpots(payload);
            String md = Report.renderReportMd(List.of(), Map.of(), "en", null, blindSpots);
            String span = Report.sanitizeForMarkdown(payload);
            List<String> labelLines = new ArrayList<>();
            for (String line : md.split("\n", -1)) {
                if (line.startsWith("- ") && line.contains(span)) {
                    labelLines.add(line);
                }
            }
            assertFalse(labelLines.isEmpty(), payload);
            for (String line : labelLines) {
                assertEquals('`', line.charAt(2), line);
            }
        }
    }

    // --- Cross-engine identity (the committed vectors file) ---

    @Test
    void javaReproducesEveryCommittedVector() throws IOException {
        JsonNode vectors = loadVectorsFile().get("vectors");
        assertTrue(vectors.size() >= 500);
        for (JsonNode vector : vectors) {
            String value = vector.get("input").asText();
            String id = vector.get("id").asText();
            assertEquals(vector.get("markdown").asText(), Report.sanitizeForMarkdown(value), id);
            assertEquals(vector.get("terminal").asText(), Report.sanitizeForTerminal(value), id);
            assertEquals(vector.get("html").asText(), Report.sanitizeForHtml(value), id);
        }
    }

    // --- The findings table's control/subject fields (Java renders only these two -- no
    // evidence-ref/violation-focus rendering exists here, same pre-existing three-engine parity gap
    // the TypeScript port's own contract intent names) ---

    private static int countOccurrences(String haystack, String needle) {
        if (needle.isEmpty()) {
            return 0;
        }
        int count = 0;
        int idx = 0;
        while ((idx = haystack.indexOf(needle, idx)) != -1) {
            count++;
            idx += needle.length();
        }
        return count;
    }

    @Test
    void findingRowSanitisesControlAndSubjectFieldsInMarkdown() {
        String hostile = "ok<br>[x](evil)`y`Verdict: Conformant";
        List<Assertions.Assertion> assertions = List.of(renderLevelAssertion(hostile));
        String md = Report.renderReportMd(assertions, Map.of("conformant", 1), "en", null, null);
        assertFalse(md.contains("<br>"));
        assertFalse(java.util.Arrays.stream(md.split("\n")).anyMatch(line -> line.equals("Verdict: Conformant")));
        assertTrue(countOccurrences(md, Report.sanitizeForMarkdown(hostile)) >= 2);
    }

    @Test
    void findingRowSanitisesControlAndSubjectFieldsInHtml() {
        String hostile = "a" + RLO + "<script>alert(1)</script>" + ZWSP + "b";
        List<Assertions.Assertion> assertions = List.of(renderLevelAssertion(hostile));
        String html = Report.renderReportHtml(assertions, Map.of("conformant", 1), "en", null, null);
        assertFalse(html.contains("<script>"));
        assertFalse(Report.hasInvisibleCodepoint(unescapeHtmlEntities(html)));
    }
}
