package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
 * The report renderers are byte-identical to the reference (SPEC §9). {@code report-golden.json} holds
 * the reference engine's {@code render_*} output for the OVS-03 failed scenario; the SARIF engine name
 * is the only engine-specific field.
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

        assertEquals(golden.get("md").textValue(), Report.renderReportMd(assertions, counts, "en"));
        assertEquals(golden.get("html").textValue(), Report.renderReportHtml(assertions, counts, "en"));
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

    /** Every verdict state, the gap cap, and a control assessed for two subjects render like the reference. */
    @Test
    void verdictCasesMatchReference() throws IOException {
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
            assertEquals(verdictCase.get("md").textValue(), Report.renderReportMd(assertions, counts, "en"));
            assertEquals(verdictCase.get("html").textValue(), Report.renderReportHtml(assertions, counts, "en"));
        }
    }

    @Test
    void writeReportEmitsEveryArtifactAndAManifest(@TempDir Path outDir) throws IOException {
        List<Assertions.Assertion> assertions = ovsFailedAssertions();
        ObjectNode manifest = Report.writeReport(
                outDir, assertions, "sha256:abc", List.of("base/eu-ai-act@1"), "ecs", List.of("conformance", "p1"), List.of(), "en");

        for (String name : List.of("assertions.json", "report.md", "report.html", "oscal-ar.json", "results.sarif", "manifest.json")) {
            assertTrue(Files.exists(outDir.resolve(name)), name + " should exist");
        }
        assertEquals("agentce-java", manifest.get("engine").get("impl").textValue());
        assertTrue(manifest.get("outputs").has("assertions.json"));
        String onDisk = Files.readString(outDir.resolve("manifest.json"));
        assertTrue(onDisk.startsWith("{\n  \"agentce_manifest_version\": 1,"), "manifest is Python-style pretty JSON");
    }
}
