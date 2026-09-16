package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Test;

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
}
