package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Coverage reconciliation is byte-identical to the reference (SPEC §6.5, §8.3): the {@code ok}/{@code
 * gap}/{@code unknown} roll-ups, repeating-decimal ratio rounding, and a manifest-declared denominator.
 */
class CoverageTest {

    @Test
    void coverageReconciliationMatchesGolden() {
        JsonNode fixture = Json.parseFile(TestPaths.testData().resolve("coverage-fixture.json"));
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("coverage-golden.json"));
        Profile profile = Profile.fromDict(fixture.get("profile"));
        JsonNode result = Coverage.computeCoverage(
                Fixtures.toList(fixture.get("events")), profile, TestPaths.testData().resolve("coverage-bundle"));
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(result));
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

        // Both shapes of escape -- a literal '..' and an innocent-looking path that is a symlink out --
        // fall back to the source's own counts, exactly as a missing manifest does.
        assertEquals(
                Map.of("ToolCall", 3),
                Coverage.denominatorCounts(denominator("../secret.json"), BY_SOURCE, bundleRoot));
        assertEquals(
                Map.of("ToolCall", 3),
                Coverage.denominatorCounts(denominator("reference/counts.json"), BY_SOURCE, bundleRoot));
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
                Map.of("ToolCall", 7),
                Coverage.denominatorCounts(denominator("reference/counts.json"), BY_SOURCE, bundleRoot));
    }
}
