package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
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
}
