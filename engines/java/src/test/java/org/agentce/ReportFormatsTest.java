package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

/**
 * report.junit.xml, report.csv, oscal-ar.xml and public-statement.md match the Python reference byte for byte
 * (SPEC §9, §9.4, §9.5; 18.16c). {@code tools/fixtures/report_formats/expected.json} is Python's rendering of each
 * case in {@code cases.json}; VG-REPORT-FORMAT-PARITY keeps it current and also compares the corpus.
 */
class ReportFormatsTest {
    private static final Path FIXTURES = TestPaths.repoRoot().resolve("tools/fixtures/report_formats");

    private static JsonNode read(String name) throws IOException {
        return Json.readBundled(Files.readAllBytes(FIXTURES.resolve(name)));
    }

    @TestFactory
    Stream<DynamicTest> reportFormatsMatchPython() throws IOException {
        JsonNode expected = read("expected.json");
        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : read("cases.json").get("cases")) {
            tests.add(DynamicTest.dynamicTest(c.get("name").asText(), () -> {
                List<Assertions.Assertion> assertions = new ArrayList<>();
                c.get("assertions").forEach(a -> assertions.add(Assertions.fromJson(a)));
                List<String> catalogs = new ArrayList<>();
                c.get("catalogs").forEach(label -> catalogs.add(label.asText()));
                JsonNode want = expected.get(c.get("name").asText());
                assertNotNull(want, "expected.json has no " + c.get("name").asText());
                assertEquals(want.get("report.junit.xml").asText(), Report.renderJunit(assertions));
                assertEquals(want.get("report.csv").asText(), Report.renderCsv(assertions));
                JsonNode oscal = c.has("oscal") ? c.get("oscal") : Report.renderOscal(assertions);
                assertEquals(want.get("oscal-ar.xml").asText(), Report.renderOscalXml(oscal));
                assertEquals(want.get("public-statement.md").asText(), Report.renderPublicStatement(
                        assertions, catalogs, Report.appliedDeviationIds(assertions)));
            }));
        }
        return tests.stream();
    }

    @Test
    void everyCaseHasAnExpectedRendering() throws IOException {
        TreeSet<String> names = new TreeSet<>();
        read("cases.json").get("cases").forEach(c -> names.add(c.get("name").asText()));
        TreeSet<String> keys = new TreeSet<>();
        read("expected.json").fieldNames().forEachRemaining(keys::add);
        assertEquals(names, keys);
    }
}
