package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@code report --validate} at parity with the Python reference (item 18.27); every case here
 * reproduces one of {@code harness/remediation/evidence/P18-18.27/python-reference.md}'s numbered
 * captures, mirroring {@code engines/typescript/src/reportValidate.test.ts}.
 */
class ReportValidateTest {
    private static final Path REPO = TestPaths.repoRoot();
    private static final Path QUICKSTART = REPO.resolve("corpus/quickstart");
    private static final Path CATALOG_DIR = REPO.resolve("spec/catalogs/base/eu-ai-act");
    private static final List<String> SCHEMA_NAMES = List.of(
            "activity", "assertions", "auditor", "blind-spots", "buyer", "manifest",
            "oscal-assessment-results", "project", "results-sarif", "security");

    @Test
    void everyLocalReportSchemaIsByteIdenticalToSpecReport() throws IOException {
        for (String name : SCHEMA_NAMES) {
            String vendored = Files.readString(
                    REPO.resolve("engines/java/src/main/resources/schemas/" + name + ".schema.json"));
            String spec = Files.readString(REPO.resolve("spec/report/" + name + ".schema.json"));
            assertEquals(spec, vendored, name + ".schema.json has drifted from spec/report");
        }
    }

    @Test
    void theTwoVendoredThirdPartySchemasAreByteIdenticalToSpecReportVendor() throws IOException {
        for (String name : List.of("oscal-assessment-results-nist-1.1.2", "sarif-2.1.0")) {
            String vendored = Files.readString(
                    REPO.resolve("engines/java/src/main/resources/schemas/" + name + ".schema.json"));
            String spec = Files.readString(REPO.resolve("spec/report/vendor/" + name + ".schema.json"));
            assertEquals(spec, vendored, name + ".schema.json has drifted from spec/report/vendor");
        }
    }

    private static JsonNode runJson(String... args) {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        PrintStream original = System.out;
        System.setOut(new PrintStream(buf, true, StandardCharsets.UTF_8));
        try {
            String[] withJson = new String[args.length + 1];
            System.arraycopy(args, 0, withJson, 0, args.length);
            withJson[args.length] = "--json";
            Cli.run(withJson);
        } finally {
            System.setOut(original);
        }
        return Json.parse(buf.toString(StandardCharsets.UTF_8));
    }

    private static void freshFullReport(Path out) {
        JsonNode env = runJson(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--catalog-dir", CATALOG_DIR.toString(),
                "--out", out.toString());
        assertEquals("assess", env.get("command").asText());
    }

    private static ObjectNode readObject(Path path) throws IOException {
        return (ObjectNode) Json.parse(Files.readString(path, StandardCharsets.UTF_8));
    }

    @Test
    void case1CleanDirectoryValidatesWithNoProblems(@TempDir Path out) {
        freshFullReport(out);
        assertEquals(List.of(), ReportValidate.validateReport(out));
    }

    @Test
    void case2MissingMandatoryArtifactIsFlaggedTwice(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.delete(out.resolve("assertions.json"));
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.contains("assertions.json: missing"));
        assertTrue(problems.contains("assertions.json: missing (recorded in manifest.json's outputs but not on disk)"));
    }

    @Test
    void case3InvalidJsonIsReportedNotACrash(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("manifest.json"), "not json{");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.stream().anyMatch(p -> p.startsWith("manifest.json: invalid JSON")), problems.toString());
    }

    @Test
    void case4LocalSchemaViolationNamesTheMissingRequiredProperty(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("assertions.json");
        ArrayNode assertions = (ArrayNode) Json.parse(Files.readString(path));
        ((ObjectNode) assertions.get(0)).remove("control");
        Files.writeString(path, assertions.toString());
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("assertions.json:") && p.contains("control")),
                problems.toString());
    }

    @Test
    void case5OscalMissingAssessmentResultsFailsTheLocalProfile(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("oscal-ar.json");
        ObjectNode oscal = readObject(path);
        oscal.remove("assessment-results");
        Files.writeString(path, oscal.toString());
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("oscal-ar.json:") && !p.contains("NIST")),
                problems.toString());
    }

    @Test
    void case6BadOscalUuidPassesLocalProfileButFailsTheRealNistSchema(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("oscal-ar.json");
        ObjectNode oscal = readObject(path);
        ((ObjectNode) oscal.get("assessment-results")).put("uuid", "not-a-uuid");
        Files.writeString(path, oscal.toString());
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("oscal-ar.json (NIST OSCAL 1.1.2): ")),
                problems.toString());
    }

    @Test
    void case7SarifMissingRunsFailsTheLocalProfile(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("results.sarif");
        ObjectNode sarif = readObject(path);
        sarif.remove("runs");
        Files.writeString(path, sarif.toString());
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("results.sarif:") && !p.contains("OASIS")),
                problems.toString());
    }

    @Test
    void case8ExtraMessageFieldPassesLocalProfileButFailsTheRealOasisSchema(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("results.sarif");
        ObjectNode sarif = readObject(path);
        ArrayNode runs = (ArrayNode) sarif.get("runs");
        ArrayNode results = (ArrayNode) ((ObjectNode) runs.get(0)).get("results");
        if (results.isEmpty()) {
            ObjectNode fabricated = Json.nodes().objectNode();
            fabricated.put("ruleId", "x");
            fabricated.put("level", "note");
            ObjectNode message = Json.nodes().objectNode();
            message.put("text", "x");
            fabricated.set("message", message);
            fabricated.set("locations", Json.nodes().arrayNode());
            fabricated.set("partialFingerprints", Json.nodes().objectNode());
            results.add(fabricated);
        }
        ((ObjectNode) ((ObjectNode) results.get(0)).get("message")).put("bogus_field", "x");
        Files.writeString(path, sarif.toString());
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("results.sarif (OASIS SARIF 2.1.0): ")),
                problems.toString());
    }

    @Test
    void case9CorruptedReportCsvNamesEachMissingColumn(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("report.csv"), "not a csv file at all, just garbage\u0000\u0001\n");
        Path manifestPath = out.resolve("manifest.json");
        ObjectNode manifest = readObject(manifestPath);
        ((ObjectNode) manifest.get("outputs")).put("report.csv", "sha256:" + "0".repeat(64));
        Files.writeString(manifestPath, manifest.toString());
        List<String> problems = ReportValidate.validateReport(out);
        for (String column : List.of("control", "subject", "outcome", "control_version", "rung", "mode")) {
            assertTrue(problems.contains("report.csv: missing column '" + column + "'"), problems.toString());
        }
    }

    @Test
    void case10and11MalformedXmlIsReportedForEitherOptionalXmlArtifact(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("report.junit.xml"), "<not-closed>");
        Files.writeString(out.resolve("oscal-ar.xml"), "<not-closed>");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.stream().anyMatch(p -> p.startsWith("report.junit.xml: invalid XML")), problems.toString());
        assertTrue(problems.stream().anyMatch(p -> p.startsWith("oscal-ar.xml: invalid XML")), problems.toString());
    }

    @Test
    void case12and13RuntimeDriftJsonlFineWhenValidFlaggedOnFirstBadLine(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path manifestPath = out.resolve("manifest.json");
        ObjectNode manifest = readObject(manifestPath);
        ((ObjectNode) manifest.get("outputs")).put("runtime_drift.jsonl", "sha256:" + "0".repeat(64));
        Files.writeString(manifestPath, manifest.toString());

        Path driftPath = out.resolve("runtime_drift.jsonl");
        Files.writeString(driftPath, "{\"subject\": \"x\"}\n{\"subject\": \"y\"}\n");
        assertEquals(List.of(), ReportValidate.validateReport(out));

        Files.writeString(driftPath, "{\"subject\": \"x\"}\nnot json at all\n");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("runtime_drift.jsonl: line 2 is not valid JSON")),
                problems.toString());
    }

    @Test
    void case14EmptyReportMdIsFlagged(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("report.md"), "   \n");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.contains("report.md: empty"));
    }

    @Test
    void case15RecordedOutputGoingMissingIsDistinguishedFromNeverWritten(@TempDir Path out) throws IOException {
        freshFullReport(out);
        assertEquals(List.of(), ReportValidate.validateReport(out));
        Files.delete(out.resolve("report.html"));
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.contains("report.html: missing (recorded in manifest.json's outputs but not on disk)"));
    }

    @Test
    void case16ValidateGivenANonDirectoryPathRefusesBeforeOpeningAnyArtifact() {
        JsonNode env = runJson("report", "--validate", "/tmp/agentce-nope-xyz-does-not-exist");
        assertEquals(3, env.get("exit_code").asInt());
        assertEquals("input.validate_not_a_directory", env.get("error").get("message_key").asText());
    }
}
