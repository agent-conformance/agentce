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
    void aSelfClosingRootFollowedByASiblingElementIsMultipleRootElements(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("report.junit.xml"), "<?xml version=\"1.0\"?>\n<a/><b/>\n");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.stream().anyMatch(p -> p.startsWith("report.junit.xml: invalid XML")), problems.toString());
    }

    @Test
    void anUndefinedNamedEntityReferenceIsNotValidXml(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("oscal-ar.xml"), "<?xml version=\"1.0\"?>\n<a>&undefined;</a>\n");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.stream().anyMatch(p -> p.startsWith("oscal-ar.xml: invalid XML")), problems.toString());
    }

    @Test
    void theFivePredefinedXmlEntitiesAndNumericCharReferencesAreStillAccepted(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(
                out.resolve("report.junit.xml"),
                "<?xml version=\"1.0\"?>\n<a>&amp; &lt; &gt; &apos; &quot; &#65; &#x41;</a>\n");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.stream().noneMatch(p -> p.startsWith("report.junit.xml:")), problems.toString());
    }

    @Test
    void aNonObjectManifestIsATidyProblemNotACrash(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.writeString(out.resolve("manifest.json"), "[1, 2, 3]");
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(problems.stream().anyMatch(p -> p.contains("manifest.json")), problems.toString());
    }

    @Test
    void nonUtf8BytesAreATidyProblemNotACrash(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Files.write(out.resolve("assertions.json"), new byte[] {(byte) 0xff, (byte) 0xfe, 0, 1, 'x'});
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("assertions.json: cannot read")), problems.toString());
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
    void aNonUtf8RuntimeDriftLineIsATidyProblemNotACrash(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path manifestPath = out.resolve("manifest.json");
        ObjectNode manifest = readObject(manifestPath);
        ((ObjectNode) manifest.get("outputs")).put("runtime_drift.jsonl", "sha256:" + "0".repeat(64));
        Files.writeString(manifestPath, manifest.toString());

        Files.write(
                out.resolve("runtime_drift.jsonl"),
                "{\"subject\": \"x\"}\n".getBytes(StandardCharsets.UTF_8));
        Files.write(
                out.resolve("runtime_drift.jsonl"),
                new byte[] {(byte) 0xff, (byte) 0xfe, 'x'},
                java.nio.file.StandardOpenOption.APPEND);
        List<String> problems = ReportValidate.validateReport(out);
        assertTrue(
                problems.stream().anyMatch(p -> p.startsWith("runtime_drift.jsonl: cannot read")),
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

    @Test
    void case22TwoLocalStageViolationsInOneFileAreBothReportedLocated(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("assertions.json");
        ArrayNode assertions = (ArrayNode) Json.parse(Files.readString(path));
        ((ObjectNode) assertions.get(0)).remove("control");
        ((ObjectNode) assertions.get(1)).remove("control");
        Files.writeString(path, assertions.toString());
        List<String> problems = ReportValidate.validateReport(out).stream()
                .filter(p -> p.startsWith("assertions.json:"))
                .toList();
        assertEquals(
                List.of(
                        "assertions.json: 0: $[0]: required property 'control' not found",
                        "assertions.json: 1: $[1]: required property 'control' not found"),
                problems);
    }

    @Test
    void case23AnyOfFailureAtRealSchemaStageCollapsesToOneCombinatorProblem(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("results.sarif");
        ObjectNode sarif = readObject(path);
        ArrayNode runs = (ArrayNode) sarif.get("runs");
        ArrayNode results = (ArrayNode) ((ObjectNode) runs.get(0)).get("results");
        ObjectNode physicalLocation = (ObjectNode) ((ObjectNode) ((ArrayNode) ((ObjectNode) results.get(0)).get("locations"))
                .get(0))
                .get("physicalLocation");
        physicalLocation.set("region", Json.nodes().objectNode());
        Files.writeString(path, sarif.toString());
        List<String> problems = ReportValidate.validateReport(out).stream()
                .filter(p -> p.startsWith("results.sarif (OASIS SARIF 2.1.0):"))
                .toList();
        assertEquals(
                List.of(
                        "results.sarif (OASIS SARIF 2.1.0): runs/0/results/0/locations/0/physicalLocation/region: "
                                + "does not match any of the required alternatives"),
                problems);
    }

    @Test
    void case24RealSchemaViolationsAtIndices2And10SortNumerically(@TempDir Path out) throws IOException {
        freshFullReport(out);
        Path path = out.resolve("results.sarif");
        ObjectNode sarif = readObject(path);
        ArrayNode runs = (ArrayNode) sarif.get("runs");
        ArrayNode results = (ArrayNode) ((ObjectNode) runs.get(0)).get("results");
        // The lightweight fixture this suite's own `assess` run produces may have fewer than 11
        // results; pad it with clones of the last one so indices 2 and 10 both exist.
        while (results.size() <= 10) {
            results.add(results.get(results.size() - 1).deepCopy());
        }
        ((ObjectNode) ((ObjectNode) results.get(2)).get("message")).remove("text");
        ((ObjectNode) ((ObjectNode) results.get(10)).get("message")).remove("text");
        Files.writeString(path, sarif.toString());
        List<String> problems = ReportValidate.validateReport(out).stream()
                .filter(p -> p.startsWith("results.sarif:"))
                .toList();
        assertEquals(
                List.of(
                        "results.sarif: runs/0/results/2/message: $.runs[0].results[2].message: required property 'text' not found",
                        "results.sarif: runs/0/results/10/message: $.runs[0].results[10].message: required property 'text' not found"),
                problems);
    }

    // P18-18.27 verifier round 2's D1-D6 shapes, pinned to the same lists as the Python and TypeScript
    // engines' own tests: one problem per failing combinator (also through `$ref`) and per object with
    // unexpected keys, `format` never asserted, object keys ordered as strings by code point.

    private static final String NIST = "oscal-ar.json (NIST OSCAL 1.1.2)";
    private static final String SARIF = "results.sarif (OASIS SARIF 2.1.0)";
    private static final String STATUS = "assessment-results/results/0/findings/0/target/status";

    /** Report `out` after `edit` changes `file`'s JSON object at JSON pointer `at`. */
    private static List<String> validateAfter(Path out, String file, String at,
            java.util.function.Consumer<ObjectNode> edit) throws IOException {
        freshFullReport(out);
        Path path = out.resolve(file);
        JsonNode document = Json.parse(Files.readString(path, StandardCharsets.UTF_8));
        edit.accept((ObjectNode) document.at(at));
        Files.writeString(path, document.toString());
        return ReportValidate.validateReport(out);
    }

    /** `"<label>: <location>"` of each problem under `label` (message text dropped). */
    private static List<String> locations(List<String> problems, String label) {
        String prefix = label + ": ";
        return problems.stream()
                .filter(p -> p.startsWith(prefix))
                .map(p -> prefix + p.substring(prefix.length()).split(": ", 2)[0])
                .toList();
    }

    @Test
    void d1CombinatorThroughRefIsOneProblem(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "oscal-ar.json", "/assessment-results/results/0/findings/0/target/status",
                o -> o.put("reason", "has space"));
        assertEquals(List.of(NIST + ": " + STATUS + "/reason"), locations(problems, NIST));
    }

    @Test
    void d2OneOfMatchingTwoBranchesIsOneProblem(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "results.sarif", "/runs/0/results/0",
                o -> o.putArray("graphTraversals").addObject().put("runGraphIndex", 0).put("resultGraphIndex", 0));
        assertEquals(List.of(SARIF + ": runs/0/results/0/graphTraversals/0"), locations(problems, SARIF));
    }

    @Test
    void d3TwoUnexpectedKeysAreOneProblem(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "results.sarif", "/runs/0/results/0/message",
                o -> o.put("bogus2", 1).put("bogus1", 1));
        assertEquals(List.of(SARIF + ": runs/0/results/0/message"), locations(problems, SARIF));
    }

    @Test
    void d3LocalStageTwoUnexpectedKeysAreOneProblem(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "assertions.json", "/0", o -> o.put("zz", 1).put("aa", 2));
        assertEquals(List.of("assertions.json: 0"), locations(problems, "assertions.json"));
    }

    @Test
    void d4FormatIsNeverAsserted(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "results.sarif", "/runs/0/tool/driver",
                o -> o.put("informationUri", "not a uri :: at all"));
        assertEquals(List.of(), problems);
    }

    @Test
    void d5d6ObjectKeysSortAsStringsByCodePoint(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "manifest.json", "/outputs", o -> {
            for (String key : List.of("9", "10", "packs/x.json", "packs-old.json")) {
                o.put(key, "bad");
            }
        });
        assertEquals(
                List.of("manifest.json: outputs/10", "manifest.json: outputs/9",
                        "manifest.json: outputs/packs-old.json", "manifest.json: outputs/packs/x.json"),
                locations(problems, "manifest.json"));
    }

    @Test
    void twoKeywordsAtOneLocationOrderByKeyword(@TempDir Path out) throws IOException {
        List<String> problems = validateAfter(out, "oscal-ar.json", "/assessment-results/results/0/findings/0/target/status",
                o -> o.put("state", "bad state"));
        assertEquals(List.of(NIST + ": " + STATUS + "/state", NIST + ": " + STATUS + "/state"), locations(problems, NIST));
        assertTrue(problems.get(0).contains("enumeration") && problems.get(1).contains("regex pattern"), problems.toString());
    }
}
