package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
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

    /** A minimal, schema-valid event with an export-chained integrity block, its hash already set. */
    private static ObjectNode buildEvent(String id, String agentClass) {
        ObjectNode integrity = Json.nodes().objectNode();
        integrity.put("hash", "");
        integrity.put("prev", Integrity.GENESIS_PREV);
        integrity.put("stream", "s-" + id);
        integrity.put("strength", "export_chained");
        ObjectNode data = Json.nodes().objectNode();
        data.put("@context", "https://agent-conformance.org/contexts/evidence/v1");
        data.put("@type", "SessionStart");
        data.set("integrity", integrity);
        data.put("session_id", "sess-" + id);
        ObjectNode event = Json.nodes().objectNode();
        event.put("agentcesourceclass", agentClass);
        event.set("data", data);
        event.put("datacontenttype", "application/ld+json");
        event.put("id", id);
        event.put("source", SOURCE);
        event.put("specversion", "1.0");
        event.put("subject", "spiffe://corp/agents/test");
        event.put("time", "2026-05-01T08:00:00.000Z");
        event.put("type", "org.agent-conformance.evidence.SessionStart.v1");
        integrity.put("hash", Integrity.recomputeHash(event));
        return event;
    }

    /**
     * Writes a bundle with one event. {@code declaredClass} is the manifest's {@code sources[].class}
     * for {@code SOURCE}; {@code null} with {@code listSource} true declares the source but names no
     * class for it (undeclared, SPEC §6.4); {@code null} with {@code listSource} false omits the
     * manifest's {@code sources} array entirely (every source implicitly undeclared).
     */
    private static Bundle writeBundle(Path tempDir, JsonNode event, String declaredClass, boolean listSource)
            throws IOException {
        Path root = tempDir.resolve("bundle");
        Files.createDirectories(root.resolve("events"));
        byte[] content = (Json.compact(event) + "\n").getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("events/log.jsonl"), content);

        ObjectNode manifest = Json.nodes().objectNode();
        ArrayNode files = manifest.putArray("files");
        ObjectNode fileEntry = files.addObject();
        fileEntry.put("path", "events/log.jsonl");
        fileEntry.put("sha256", "sha256:" + Canonical.sha256Hex(content));
        if (declaredClass != null || listSource) {
            ObjectNode sourceEntry = manifest.putArray("sources").addObject();
            sourceEntry.put("id", SOURCE);
            if (declaredClass != null) {
                sourceEntry.put("class", declaredClass);
            }
        }
        Files.writeString(root.resolve("manifest.json"), Json.compact(manifest), StandardCharsets.UTF_8);
        return Bundle.load(root);
    }

    @Test
    void sourceDeclaredWithoutAClassDefaultsToSelfReport(@TempDir Path tempDir) throws IOException {
        // The manifest names no class for this source at all (SPEC §6.4): the event's own
        // self-assertion ("enforcement_point") is never trusted as-is.
        JsonNode event = buildEvent("e1", "enforcement_point");
        Bundle bundle = writeBundle(tempDir, event, null, true);
        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(1, result.accepted.size());
        assertEquals(List.of(), result.quarantined);
        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
    }

    @Test
    void bundleWithNoDeclaredSourcesAtAllDefaultsToSelfReport(@TempDir Path tempDir) throws IOException {
        // No manifest "sources" entries at all: every source is implicitly undeclared, so every
        // event's self-asserted class is corrected the same way as a source named without a class.
        JsonNode event = buildEvent("e1", "enforcement_point");
        Bundle bundle = writeBundle(tempDir, event, null, false);
        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(1, result.accepted.size());
        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
    }

    @Test
    void undeclaredSourceAlreadySelfReportIsUnchanged(@TempDir Path tempDir) throws IOException {
        JsonNode event = buildEvent("e1", "self_report");
        Bundle bundle = writeBundle(tempDir, event, null, true);
        Ingest.Result result = Ingest.ingest(bundle);
        assertEquals(1, result.accepted.size());
        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
        // No correction was needed, so the accepted and raw copies are the identical object.
        assertSame(result.accepted.get(0), result.rawAccepted.get(0));
    }

    @Test
    void classMatchIsAcceptedKeepsRawAndAcceptedIdentical(@TempDir Path tempDir) throws IOException {
        JsonNode event = buildEvent("e1", "enforcement_point");
        Bundle bundle = writeBundle(tempDir, event, "enforcement_point", true);
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
        JsonNode event = buildEvent("e1", "enforcement_point");
        Bundle bundle = writeBundle(tempDir, event, null, true);
        Ingest.Result result = Ingest.ingest(bundle);

        assertEquals("self_report", result.accepted.get(0).get("agentcesourceclass").asText());
        assertEquals("enforcement_point", result.rawAccepted.get(0).get("agentcesourceclass").asText());
        assertEquals(event, result.rawAccepted.get(0));

        List<Integrity.Result> verified = Integrity.verifyBundle(result.rawAccepted, bundle.manifest, bundle.root);
        assertEquals(List.of("verified_weak"), verified.stream().map(r -> r.status).toList());

        List<Integrity.Result> tampered = Integrity.verifyBundle(result.accepted, bundle.manifest, bundle.root);
        assertEquals(List.of("failed"), tampered.stream().map(r -> r.status).toList());
    }
}
