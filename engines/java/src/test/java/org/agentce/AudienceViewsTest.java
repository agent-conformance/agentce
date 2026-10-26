package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The audience-view registry and the security view's renderers match Python (18.16g): every case of
 * security_cases.json renders security_expected.json's text, the dispatch writes security.md and
 * security.html only when asked (inbox row 106 M2), and writeReport writes the security files only
 * while the preset is registered (M3). The buyer view (18.16h): every case of buyer_cases.json renders
 * buyer_expected.json's text, buyer.md and buyer.html are always written, and compute over an assessed
 * run's assertions and blind spots gives Python's buyer.json.
 */
class AudienceViewsTest {
    private static final List<String> SECURITY_FILES = List.of("security.json", "security.md", "security.html");

    private static JsonNode fixture(String name) throws IOException {
        return Json.readBundled(Files.readAllBytes(
                TestPaths.repoRoot().resolve("tools/fixtures/report_formats").resolve(name)));
    }

    @Test
    void everyCaseRendersPythonsText() throws IOException {
        JsonNode expected = fixture("security_expected.json");
        TreeSet<String> names = new TreeSet<>();
        List<String> mismatches = new ArrayList<>();
        for (JsonNode c : fixture("security_cases.json").get("cases")) {
            String name = c.get("name").asText();
            String language = c.get("language").asText();
            names.add(name + ".md");
            names.add(name + ".html");
            if (!expected.path(name + ".md").asText().equals(Report.renderSecurityMd(c.get("view"), language))) {
                mismatches.add(name + ".md");
            }
            if (!expected.path(name + ".html").asText().equals(Report.renderSecurityHtml(c.get("view"), language))) {
                mismatches.add(name + ".html");
            }
        }
        TreeSet<String> want = new TreeSet<>();
        expected.fieldNames().forEachRemaining(want::add);
        assertEquals(want, names);
        assertEquals(List.of(), mismatches);
    }

    @Test
    void registryKeysAreExactlySecurity() {
        assertEquals(new TreeSet<>(List.of("buyer", "security")), new TreeSet<>(Report.AUDIENCE_VIEWS.keySet()));
    }

    private static List<String> written(String preset, List<String> wanted) throws IOException {
        List<String> names = new ArrayList<>();
        Report.writeAudienceView(
                preset,
                new Report.ReportViewContext(
                        Activity.summarizeActivity(List.of(), new Profile()), List.of(), "en", java.util.Map.of(),
                        Json.nodes().objectNode(), List.of(), false, "out", List.of()),
                wanted::contains,
                (name, view) -> names.add(name),
                (name, text) -> names.add(name));
        return names;
    }

    @Test
    void securityWritesMdAndHtmlOnlyWhenAsked() throws IOException {
        assertEquals(List.of("security.json"), written("security", List.of()));
        assertEquals(List.of("security.json", "security.md"), written("security", List.of("md")));
        assertEquals(List.of("security.json", "security.html"), written("security", List.of("html")));
        assertEquals(SECURITY_FILES, written("security", List.of("md", "html")));
    }

    @Test
    void unknownOrNoPresetWritesNothing() throws IOException {
        assertEquals(List.of(), written("nonsense", List.of("md", "html")));
        assertEquals(List.of(), written(null, List.of("md", "html")));
    }

    private static ObjectNode reportWith(Path dir, String preset) {
        Report.SEAM_FOR = preset;
        try {
            return Report.writeReport(
                    dir, List.of(), "sha256:abc", List.of(), "x", List.of(), List.of(), "en", List.of(), null, null);
        } finally {
            Report.SEAM_FOR = null;
        }
    }

    @Test
    void writeReportWritesSecurityFilesOnlyWhileRegistered(@TempDir Path tmp) {
        Path registered = tmp.resolve("registered");
        ObjectNode manifest = reportWith(registered, "security");
        for (String name : SECURITY_FILES) {
            assertTrue(Files.isRegularFile(registered.resolve(name)), name);
            assertTrue(manifest.get("outputs").has(name), name);
        }

        Report.AudienceViewSpec spec = Report.AUDIENCE_VIEWS.remove("security");
        try {
            Path removed = tmp.resolve("removed");
            ObjectNode without = reportWith(removed, "security");
            for (String name : SECURITY_FILES) {
                assertFalse(Files.exists(removed.resolve(name)), name);
                assertFalse(without.get("outputs").has(name), name);
            }
        } finally {
            Report.AUDIENCE_VIEWS.put("security", spec);
        }
    }

    @Test
    void writeReportWritesNoSecurityFileWithNoOrUnknownPreset(@TempDir Path tmp) {
        for (String preset : new String[] {null, "nonsense"}) {
            Path dir = tmp.resolve(String.valueOf(preset));
            ObjectNode manifest = reportWith(dir, preset);
            for (String name : SECURITY_FILES) {
                assertFalse(Files.exists(dir.resolve(name)), name);
                assertFalse(manifest.get("outputs").has(name), name);
            }
        }
    }

    private static final List<String> BUYER_FILES = List.of("buyer.json", "buyer.md", "buyer.html");

    @Test
    void everyBuyerCaseRendersPythonsText() throws IOException {
        JsonNode expected = fixture("buyer_expected.json");
        TreeSet<String> names = new TreeSet<>();
        List<String> mismatches = new ArrayList<>();
        for (JsonNode c : fixture("buyer_cases.json").get("cases")) {
            String name = c.get("name").asText();
            List<String> argv = new ArrayList<>();
            c.get("argv").forEach(a -> argv.add(a.asText()));
            List<String> catalogs = new ArrayList<>();
            c.get("catalogs").forEach(pair -> catalogs.add(pair.get(0).asText() + "@" + pair.get(1).asText()));
            Report.BuyerRenderOptions options = new Report.BuyerRenderOptions(
                    c.get("language").asText(), argv, c.get("packaged").asBoolean(), c.get("folder").asText(), catalogs);
            names.add(name + ".md");
            names.add(name + ".html");
            if (!expected.path(name + ".md").asText().equals(Report.renderBuyerMd(c.get("view"), options))) {
                mismatches.add(name + ".md");
            }
            if (!expected.path(name + ".html").asText().equals(Report.renderBuyerHtml(c.get("view"), options))) {
                mismatches.add(name + ".html");
            }
        }
        TreeSet<String> want = new TreeSet<>();
        expected.fieldNames().forEachRemaining(want::add);
        assertEquals(want, names);
        assertEquals(List.of(), mismatches);
    }

    @Test
    void buyerAlwaysWritesMdAndHtml() {
        for (List<String> wanted : List.of(List.<String>of(), List.of("md"), List.of("html"), List.of("md", "html"))) {
            List<String> names = new ArrayList<>();
            try {
                Report.writeAudienceView(
                        "buyer",
                        new Report.ReportViewContext(
                                Activity.summarizeActivity(List.of(), new Profile()), List.of(), "en",
                                java.util.Map.of(), Json.nodes().objectNode(), List.of(), false, "out", List.of()),
                        wanted::contains,
                        (name, view) -> names.add(name),
                        (name, text) -> names.add(name));
            } catch (IOException e) {
                throw new AssertionError(e);
            }
            assertEquals(BUYER_FILES, names);
        }
    }

    @Test
    void writeReportWritesBuyerFilesWithSeamForBuyer(@TempDir Path tmp) {
        Path dir = tmp.resolve("buyer");
        ObjectNode manifest = reportWith(dir, "buyer");
        for (String name : BUYER_FILES) {
            assertTrue(Files.isRegularFile(dir.resolve(name)), name);
            assertTrue(manifest.get("outputs").has(name), name);
        }
    }

    @Test
    void computeBuyerViewGivesAnAssessedRunsBuyerJson() throws IOException {
        List<Assertions.Assertion> assertions = new ArrayList<>();
        fixture("buyer_run/assertions.json").forEach(a -> assertions.add(Assertions.fromJson(a)));
        ObjectNode view = BuyerView.computeBuyerView(assertions, fixture("buyer_run/blind-spots.json"));
        assertEquals(
                new String(Files.readAllBytes(TestPaths.repoRoot().resolve(
                        "tools/fixtures/report_formats/buyer_run/buyer.json")), java.nio.charset.StandardCharsets.UTF_8),
                new String(Canonical.canonicalize(view), java.nio.charset.StandardCharsets.UTF_8));
    }
}
