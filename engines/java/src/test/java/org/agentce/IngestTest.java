package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Bundle loading, schema validation, and ingest quarantine (cross-checked with the reference). */
class IngestTest {
    private static final Path BUNDLE = TestPaths.testData().resolve("ingest-bundle");
    private static final String SOURCE = "urn:agentce:source:crewai-session:eu-1";

    @Test
    void bundleDigestIsTheCanonicalSha256OfTheManifest() {
        assertEquals(
                "sha256:3b1a171abe9f0d3f1945a353c4b608d1f7e8bbaf526053f7280c9f92d8ee94be",
                Bundle.load(BUNDLE).digest());
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

    @Test
    void sourceDeclaredWithoutAClassDefaultsToSelfReport(@TempDir Path tempDir) throws IOException {
        // The manifest names no class for this source at all (SPEC §6.4): the event's own
        // self-assertion ("enforcement_point") is never trusted as-is.
        JsonNode event = Fixtures.buildIngestEvent(SOURCE, "enforcement_point");
        Bundle bundle = Fixtures.writeIngestBundle(tempDir.resolve("bundle"), event, SOURCE, null);
        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(1, result.accepted.size());
        assertEquals(List.of(), result.quarantined);
        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
    }

    @Test
    void bundleWithNoDeclaredSourcesAtAllDefaultsToSelfReport(@TempDir Path tempDir) throws IOException {
        // No manifest "sources" entries at all: every source is implicitly undeclared, so every
        // event's self-asserted class is corrected the same way as a source named without a class.
        // (Its own small manifest write, not Fixtures.writeIngestBundle: that helper always lists the
        // source, which is the shape every other test here needs.)
        JsonNode event = Fixtures.buildIngestEvent(SOURCE, "enforcement_point");
        Path root = tempDir.resolve("bundle");
        Files.createDirectories(root.resolve("events"));
        byte[] content = (Json.compact(event) + "\n").getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("events/log.jsonl"), content);
        String manifest = "{\"files\":[{\"path\":\"events/log.jsonl\",\"sha256\":"
                + Json.quote("sha256:" + Canonical.sha256Hex(content)) + "}]}";
        Files.writeString(root.resolve("manifest.json"), manifest, StandardCharsets.UTF_8);

        Ingest.Result result = Ingest.ingest(Bundle.load(root));
        assertEquals(1, result.accepted.size());
        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
    }

    @Test
    void undeclaredSourceAlreadySelfReportIsUnchanged(@TempDir Path tempDir) throws IOException {
        JsonNode event = Fixtures.buildIngestEvent(SOURCE, "self_report");
        Bundle bundle = Fixtures.writeIngestBundle(tempDir.resolve("bundle"), event, SOURCE, null);
        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(1, result.accepted.size());
        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
        // No correction was needed, so the accepted and raw copies are the identical object.
        assertSame(result.accepted.get(0), result.rawAccepted.get(0));
    }

    @Test
    void classMatchIsAcceptedKeepsRawAndAcceptedIdentical(@TempDir Path tempDir) throws IOException {
        JsonNode event = Fixtures.buildIngestEvent(SOURCE, "enforcement_point");
        Bundle bundle =
                Fixtures.writeIngestBundle(tempDir.resolve("bundle"), event, SOURCE, "enforcement_point");
        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(1, result.accepted.size());
        assertSame(result.accepted.get(0), result.rawAccepted.get(0));
    }

    @Test
    void undeclaredSourceCorrectionNeverReachesRawAccepted(@TempDir Path tempDir) throws IOException {
        // The correction that makes `accepted` honest must never change the bytes integrity
        // verification hashes (SPEC §6.6 hashes the whole CloudEvent, `agentcesourceclass` included,
        // as the source emitted it): `rawAccepted` stays byte-identical so a genuinely unmodified,
        // undeclared-source event still verifies, while the corrected copy would wrongly look tampered.
        JsonNode event = Fixtures.buildIngestEvent(SOURCE, "enforcement_point");
        Bundle bundle = Fixtures.writeIngestBundle(tempDir.resolve("bundle"), event, SOURCE, null);
        Ingest.Result result = Ingest.ingest(bundle);

        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
        assertEquals("enforcement_point", result.rawAccepted.get(0).get("agentcesourceclass").asText());
        assertEquals(event, result.rawAccepted.get(0));

        List<Integrity.Result> verified = Integrity.verifyBundle(result.rawAccepted, bundle.manifest, bundle.root);
        assertEquals(List.of("verified_weak"), verified.stream().map(r -> r.status).toList());

        List<Integrity.Result> tampered = Integrity.verifyBundle(result.accepted, bundle.manifest, bundle.root);
        assertEquals(List.of("failed"), tampered.stream().map(r -> r.status).toList());
    }

    @Test
    void manifestClassEmptyNullOrNonStringIsTreatedAsUndeclared(@TempDir Path tempDir) throws IOException {
        // A manifest entry naming a source is not the same as declaring its class (SPEC §6.4): an
        // empty string, JSON null, or a non-string value must get the same honest default as a source
        // with no `class` key at all, not be coerced or rejected.
        String[] sources = {
            "urn:agentce:source:undeclared-empty",
            "urn:agentce:source:undeclared-null",
            "urn:agentce:source:undeclared-number",
        };
        List<JsonNode> events = new ArrayList<>();
        for (int i = 0; i < sources.length; i++) {
            events.add(Fixtures.buildIngestEvent(sources[i], "enforcement_point", "e" + i, "s" + i));
        }

        ArrayNode sourceArray = Json.nodes().arrayNode();
        sourceArray.addObject().put("id", sources[0]).put("class", "");
        sourceArray.addObject().put("id", sources[1]).putNull("class");
        sourceArray.addObject().put("id", sources[2]).put("class", 42);
        Bundle bundle = Fixtures.writeIngestBundle(tempDir.resolve("bundle"), events, sourceArray);

        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(3, result.accepted.size());
        assertEquals(List.of(), result.quarantined);
        for (JsonNode event : result.accepted) {
            assertEquals("self_report", event.get("agentcesourceclass").asText());
        }
    }
}
