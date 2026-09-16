package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Bundle loading, schema validation, and ingest quarantine (cross-checked with the reference). */
class IngestTest {
    private static final Path BUNDLE = TestPaths.testData().resolve("ingest-bundle");

    @Test
    void bundleDigestIsTheCanonicalSha256OfTheManifest() {
        assertEquals(
                "sha256:3b1a171abe9f0d3f1945a353c4b608d1f7e8bbaf526053f7280c9f92d8ee94be",
                Bundle.load(BUNDLE).digest);
    }

    @Test
    void ingestAcceptsValidEventsAndQuarantinesTheRest() {
        Ingest.Result result = Ingest.ingest(Bundle.load(BUNDLE));
        assertEquals(1, result.accepted.size());
        Map<String, Integer> counts = Quarantine.countsByReason(result.quarantined);
        assertEquals(Map.of("duplicate_id", 1, "schema_invalid", 1), counts);
    }

    @Test
    void loadBundleRefusesABundleWithNoManifest() {
        InputError error = assertThrows(InputError.class, () -> Bundle.load(BUNDLE.resolve("events")));
        assertEquals("input.bundle_manifest_missing", error.key);
    }
}
