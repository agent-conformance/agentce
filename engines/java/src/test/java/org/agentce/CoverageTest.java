package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Coverage reconciliation is byte-identical to the reference (SPEC §6.5, §8.3): the {@code ok}/{@code
 * gap}/{@code unknown} roll-ups, repeating-decimal ratio rounding, and a manifest-declared denominator.
 */
class CoverageTest {

    @Test
    void coverageReconciliationMatchesGolden() throws IOException {
        JsonNode fixture = Json.parseFile(TestPaths.testData().resolve("coverage-fixture.json"));
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("coverage-golden.json"));
        Profile profile = Profile.fromDict(fixture.get("profile"));
        JsonNode result = Coverage.computeCoverage(
                Fixtures.toList(fixture.get("events")),
                profile,
                membersOf(TestPaths.testData().resolve("coverage-bundle"), "reference/prov-counts.json"));
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(result));
    }

    /** A subject that declares no evidence source, so every denominator here is independent. */
    private static final Profile.Subject SUBJECT = new Profile.Subject();

    /** The bundle manifest lists the denominator manifest, so its counts may be read (SPEC §6.5). */
    private static final String LISTED = "reference/counts.json";

    /**
     * The checked bytes Bundle.load hands coverage for the listed paths that stay inside {@code root} (Bundle.load
     * refuses a bundle with one that escapes, so its bytes never reach coverage: 18.184f).
     */
    private static Map<String, byte[]> membersOf(Path root, String... listed) throws IOException {
        Map<String, byte[]> out = new java.util.HashMap<>();
        for (String rel : listed) {
            Path member = Bundle.confineToRoot(root, rel);
            if (member != null && Bundle.safeIsFile(member)) {
                out.put(rel, Files.readAllBytes(member));
            }
        }
        return out;
    }

    private static Profile.CoverageDenominator denominator(String manifest) {
        Profile.CoverageDenominator denominator = new Profile.CoverageDenominator();
        denominator.kind = "registry";
        denominator.source = "urn:src:egress";
        denominator.covers = List.of("ToolCall");
        denominator.manifest = manifest;
        return denominator;
    }

    /** The denominator source's own ingested events: the fallback when no manifest is readable. */
    private static final Map<String, Map<String, Integer>> BY_SOURCE =
            Map.of("urn:src:egress", Map.of("ToolCall", 3));

    @Test
    void denominatorManifestEscapingTheBundleRootIsNotRead(@TempDir Path tempDir) throws IOException {
        Path bundleRoot = tempDir.resolve("bundle");
        Files.createDirectories(bundleRoot.resolve("reference"));
        Path outside = tempDir.resolve("secret.json");
        Files.writeString(outside, "{\"expected\": {\"ToolCall\": 999999}}", StandardCharsets.UTF_8);
        Files.createSymbolicLink(bundleRoot.resolve("reference").resolve("counts.json"), outside);

        // Both shapes of escape -- a literal '..' and an innocent-looking path that is a symlink out -- are never among
        // the checked members (Bundle.load refuses the bundle), so nothing is read and nothing is counted.
        assertEquals(
                Map.of(),
                Coverage.denominatorCounts(
                        denominator("../secret.json"), SUBJECT, BY_SOURCE, membersOf(bundleRoot, "../secret.json")));
        assertEquals(
                Map.of(),
                Coverage.denominatorCounts(
                        denominator("reference/counts.json"), SUBJECT, BY_SOURCE, membersOf(bundleRoot, LISTED)));
    }

    @Test
    void denominatorManifestInsideTheBundleRootIsRead(@TempDir Path tempDir) throws IOException {
        Path bundleRoot = tempDir.resolve("bundle");
        Files.createDirectories(bundleRoot.resolve("reference"));
        Files.writeString(
                bundleRoot.resolve("reference").resolve("counts.json"),
                "{\"expected\": {\"ToolCall\": 7}}",
                StandardCharsets.UTF_8);

        assertEquals(
                Map.of("ToolCall", 7L),
                Coverage.denominatorCounts(
                        denominator("reference/counts.json"), SUBJECT, BY_SOURCE, membersOf(bundleRoot, LISTED)));
    }

    @Test
    void manifestDenominatorOwnRecordsAreNeverCapturedCalls(@TempDir Path tempDir) throws IOException {
        Path bundleRoot = tempDir.resolve("bundle");
        Files.createDirectories(bundleRoot.resolve("reference"));
        Files.writeString(
                bundleRoot.resolve("reference").resolve("counts.json"),
                "{\"counts\": {\"ToolCall\": 2}}",
                StandardCharsets.UTF_8);
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\": [{\"id\": \"subj-A\", \"coverage_denominators\": "
                + "[{\"kind\": \"egress_proxy_log\", \"source\": \"urn:src:egress\", \"covers\": [\"ToolCall\"], "
                + "\"manifest\": \"reference/counts.json\"}]}]}"));
        List<JsonNode> events = Fixtures.toList(Json.parse("["
                + "{\"source\": \"urn:src:agent\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"ToolCall\"}},"
                + "{\"source\": \"urn:src:egress\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"ToolCall\"}},"
                + "{\"source\": \"urn:src:egress\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"ToolCall\"}},"
                + "{\"source\": \"urn:src:egress\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"SessionStart\"}}]"));

        // The egress log's own ToolCall records are its yardstick, never captured calls (SPEC §6.5); its SessionStart,
        // a type it does not count, stays observed.
        JsonNode types = Coverage.computeCoverage(events, profile, membersOf(bundleRoot, LISTED))
                .get("subjects").get("subj-A").get("event_types");
        assertEquals(1, types.get("ToolCall").get("observed").asInt());
        assertEquals(2, types.get("ToolCall").get("expected").asInt());
        assertEquals("below_threshold", types.get("ToolCall").get("status").asText());
        assertEquals(1, types.get("SessionStart").get("observed").asInt());
        assertEquals("unknown", types.get("SessionStart").get("status").asText());
    }

    @Test
    void selfCountingDenominatorHidesOnlyTheTypesItCovers() {
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\": [{\"id\": \"subj-A\", \"evidence_sources\": "
                + "[{\"adapter\": \"gw\", \"source\": \"urn:src:gw\", \"class\": \"enforcement_point\"}], "
                + "\"coverage_denominators\": [{\"kind\": \"egress_proxy_log\", \"source\": \"urn:src:egress\", "
                + "\"covers\": [\"ToolCall\"]}]}]}"));
        List<JsonNode> events = Fixtures.toList(Json.parse("["
                + "{\"source\": \"urn:src:gw\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"ToolCall\"}},"
                + "{\"source\": \"urn:src:egress\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"ToolCall\"}},"
                + "{\"source\": \"urn:src:egress\", \"subject\": \"subj-A\", \"data\": {\"@type\": \"SessionStart\"}}]"));

        // The egress log covers only ToolCall, so its SessionStart is a captured record nothing counts (SPEC §6.5).
        JsonNode subject = Coverage.computeCoverage(events, profile, Map.of())
                .get("subjects").get("subj-A");
        assertEquals(1, subject.get("event_types").get("SessionStart").get("observed").asInt());
        assertEquals("unknown", subject.get("event_types").get("SessionStart").get("status").asText());
        assertEquals("unknown", subject.get("coverage_status").asText());
    }

    // --- 18.184j: every engine refuses the same manifests with one key, never a crash or a truncated count.

    private static Map<String, Long> manifestCounts(String raw) {
        return manifestCounts(raw.getBytes(StandardCharsets.UTF_8));
    }

    private static Map<String, Long> manifestCounts(byte[] raw) {
        return Coverage.denominatorCounts(denominator(LISTED), SUBJECT, BY_SOURCE, Map.of(LISTED, raw));
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "not json",
                "",
                "{\"counts\": {\"ToolCall\": NaN}}",
                "{\"counts\": {\"ToolCall\": Infinity}}",
                "{\"counts\": [3]}",
                "{\"counts\": []}",
                "{\"counts\": \"x\"}",
                "{\"counts\": null}",
                "{\"counts\": true}",
                "{\"counts\": {\"ToolCall\": 1}, \"expected\": [1]}",
                "{\"counts\": {\"ToolCall\": 1}, \"expected\": {\"ToolCall\": -1}}",
                "{\"counts\": {\"ToolCall\": \"x\"}}",
                "{\"counts\": {\"ToolCall\": \"1\"}}",
                "{\"counts\": {\"ToolCall\": -3}}",
                "{\"counts\": {\"ToolCall\": true}}",
                "{\"counts\": {\"ToolCall\": null}}",
                "{\"counts\": {\"ToolCall\": {\"n\": 1}}}",
                "{\"counts\": {\"ToolCall\": 1.0}}",
                "{\"counts\": {\"ToolCall\": 1.5}}",
                "{\"counts\": {\"ToolCall\": 1e0}}",
                "{\"counts\": {\"ToolCall\": 9007199254740992}}",
            })
    void denominatorManifestWithBadCountsIsRefusedNamingTheFile(String raw) {
        InputError error = assertThrows(InputError.class, () -> manifestCounts(raw));
        assertEquals("input.coverage_denominator_manifest_invalid", error.key);
        assertTrue(error.reason.contains(LISTED), error.reason);
    }

    @Test
    void denominatorManifestThatIsNotUtf8IsRefused() {
        byte[] good = "{\"counts\": {\"ToolCall\": 1}}".getBytes(StandardCharsets.UTF_8);
        byte[] raw = Arrays.copyOf(good, good.length + 1);
        raw[good.length] = (byte) 0xff;
        InputError error = assertThrows(InputError.class, () -> manifestCounts(raw));
        assertEquals("input.coverage_denominator_manifest_invalid", error.key);
    }

    @Test
    void denominatorManifestWholeCountsAreReadPastInt32() {
        assertEquals(Map.of("ToolCall", 0L), manifestCounts("{\"counts\": {\"ToolCall\": 0}}"));
        assertEquals(Map.of("ToolCall", 3000000000L), manifestCounts("{\"counts\": {\"ToolCall\": 3000000000}}"));
        assertEquals(
                Map.of("ToolCall", 9007199254740991L), manifestCounts("{\"counts\": {\"ToolCall\": 9007199254740991}}"));
        assertEquals(Map.of("ToolCall", 2L), manifestCounts("{\"counts\": {}, \"expected\": {\"ToolCall\": 2}}"));
        assertEquals(Map.of("ToolCall", 2L), manifestCounts("{\"expected\": {\"ToolCall\": 2}}"));
    }
}
