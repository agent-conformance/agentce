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

    @Test
    void aLowerClassRecordNeverDisplacesAHigherClassRecord(@TempDir Path tempDir) throws IOException {
        // 18.143: the agent's own record reusing the enforcement point's id, before or after it, is the one
        // quarantined; the class ingest trusts ranks, so an undeclared source asserting independent_system loses too.
        String agent = "urn:example:agent";
        JsonNode genuine = Fixtures.buildIngestEvent(SOURCE, "enforcement_point", "e1", "gw");
        JsonNode shadow = Fixtures.buildIngestEvent(agent, "self_report", "e1", "agent");
        JsonNode undeclared = Fixtures.buildIngestEvent(agent, "independent_system", "e1", "agent");
        Object[][] runs = {
            {List.of(shadow, genuine), "self_report", "a record of a higher source class has this event id"},
            {List.of(genuine, shadow), "self_report", "event id already seen in this bundle"},
            {List.of(undeclared, genuine), null, "a record of a higher source class has this event id"},
        };
        int n = 0;
        for (Object[] run : runs) {
            ArrayNode sources = Json.nodes().arrayNode();
            sources.addObject().put("id", SOURCE).put("class", "enforcement_point");
            ObjectNode agentSource = sources.addObject().put("id", agent);
            if (run[1] != null) {
                agentSource.put("class", (String) run[1]);
            }
            @SuppressWarnings("unchecked")
            List<JsonNode> events = (List<JsonNode>) run[0];
            Ingest.Result result =
                    Ingest.ingest(Fixtures.writeIngestBundle(tempDir.resolve("b" + n++), events, sources));
            assertEquals(List.of(genuine), result.accepted);
            assertEquals(1, result.quarantined.size());
            ObjectNode q = result.quarantined.get(0).toJson();
            assertEquals("duplicate_id", q.get("reason").asText());
            assertEquals(agent, q.get("source").asText());
            assertEquals(run[2], q.get("detail").asText());
        }
    }

    @Test
    void aRecordNeverJoinsAnotherSourcesTimeOrderByClaimingItsStream(@TempDir Path tempDir) throws IOException {
        // 18.147: the agent's own later record names the enforcement point's stream; time order is per source, so
        // the enforcement point's earlier record is not quarantined.
        String agent = "urn:example:agent";
        JsonNode genuine = Fixtures.buildIngestEvent(SOURCE, "enforcement_point", "e1", "gw");
        ObjectNode hijack = Fixtures.buildIngestEvent(agent, "self_report", "e2", "gw");
        hijack.put("time", "2099-01-01T00:00:00.000Z");
        ArrayNode sources = Json.nodes().arrayNode();
        sources.addObject().put("id", SOURCE).put("class", "enforcement_point");
        sources.addObject().put("id", agent).put("class", "self_report");
        Ingest.Result result =
                Ingest.ingest(Fixtures.writeIngestBundle(tempDir, List.of(hijack, genuine), sources));
        assertEquals(List.of(hijack, genuine), result.accepted);
        assertEquals(List.of(), result.quarantined);
    }

    @Test
    void timeOrderComparesTimesAsInstants(@TempDir Path tempDir) throws IOException {
        // 18.145: 00:30 two hours behind UTC is 02:30Z, after 01:00Z; 01:30 two hours ahead is 23:30Z the day before.
        ObjectNode first = Fixtures.buildIngestEvent(SOURCE, "enforcement_point", "e1", "s1");
        first.put("time", "2026-01-10T01:00:00Z");
        ObjectNode later = Fixtures.buildIngestEvent(SOURCE, "enforcement_point", "e2", "s1");
        later.put("time", "2026-01-10T00:30:00-02:00");
        ObjectNode earlier = Fixtures.buildIngestEvent(SOURCE, "enforcement_point", "e3", "s1");
        earlier.put("time", "2026-01-10T01:30:00+02:00");
        ArrayNode sources = Json.nodes().arrayNode();
        sources.addObject().put("id", SOURCE).put("class", "enforcement_point");
        Ingest.Result result =
                Ingest.ingest(Fixtures.writeIngestBundle(tempDir, List.of(first, later, earlier), sources));
        assertEquals(List.of(first, later), result.accepted);
        assertEquals(1, result.quarantined.size());
        ObjectNode q = result.quarantined.get(0).toJson();
        assertEquals("e3", q.get("event_id").asText());
        assertEquals("time_order", q.get("reason").asText());
    }

    @Test
    void withheldListsTheQuarantinedRecordsThatMayHoldEvidence(@TempDir Path tempDir) throws IOException {
        // 18.119: a record refused for where it sits stays readable (read with the declared class); one ingest
        // cannot read has no event; an identical redelivery holds nothing new.
        ObjectNode first = Fixtures.buildIngestEvent(SOURCE, "enforcement_point", "e1", "gw");
        ObjectNode late = first.deepCopy().put("id", "e2").put("time", "2020-01-01T00:00:00.000Z");
        ObjectNode invalid = first.deepCopy().put("id", "e3").put("time", 7);
        ObjectNode other = first.deepCopy().put("time", "2030-01-01T00:00:00.000Z");
        ArrayNode sources = Json.nodes().arrayNode();
        sources.addObject().put("id", SOURCE).put("class", "enforcement_point");
        Ingest.Result result = Ingest.ingest(Fixtures.writeIngestBundle(
                tempDir.resolve("b"), List.of(first, late, invalid, first.deepCopy(), other), sources));
        // File order, then each non-identical duplicate once every record is placed.
        List<String> reasons = new ArrayList<>();
        result.withheld.forEach(w -> reasons.add(w.record().toJson().get("reason").asText()));
        assertEquals(List.of("time_order", "schema_invalid", "duplicate_id"), reasons);
        assertEquals(late, result.withheld.get(0).event());
        assertEquals(null, result.withheld.get(1).event());
        assertEquals(other, result.withheld.get(2).event());
        assertEquals(4, result.quarantined.size());
    }

    @Test
    void anOversizeRecordIsWithheldWithNoEvent(@TempDir Path tempDir) throws IOException {
        JsonNode event = Fixtures.buildIngestEvent(SOURCE, "enforcement_point");
        Ingest.Result result =
                Ingest.ingest(Fixtures.writeIngestBundle(tempDir.resolve("bundle"), event, SOURCE, null), 10);
        assertEquals(1, result.withheld.size());
        assertEquals("oversize", result.withheld.get(0).record().toJson().get("reason").asText());
        assertEquals(null, result.withheld.get(0).event());
        assertEquals(null, result.withheld.get(0).subject());
    }

    private static JsonNode unreadableWrite(String id, String typeJson, String dataJson) {
        return Json.parse("{\"id\": \"" + id + "\", \"source\": \"" + SOURCE + "\", "
                + "\"subject\": \"spiffe://corp/agents/other\", \"type\": " + typeJson + ", "
                + "\"time\": \"2026-01-02T00:00:01Z\", \"specversion\": \"1.0\", "
                + "\"data\": {\"store\": \"kb\", \"record_ref\": \"mem:r1\", \"extra\": 1" + dataJson + "}}");
    }

    @Test
    void withheldKeepsTheRawFactsOfAnUnreadableWrite(@TempDir Path tempDir) throws IOException {
        // 18.172: an unreadable record is a MemoryWrite unless it names another type the engine knows; its record_ref
        // and store are kept when each is a string.
        String write = "\"org.agent-conformance.evidence.MemoryWrite.v1\"";
        String misspelt = "\"org.agent-conformance.evidence.MemoryWrit.v1\"";
        String read = "\"org.agent-conformance.evidence.MemoryRead.v1\"";
        List<JsonNode> events = List.of(
                unreadableWrite("w1", write, ", \"@type\": \"MemoryWrite\""),
                unreadableWrite("w2", "5", ""),
                unreadableWrite("w3", misspelt, ", \"@type\": \"MemoryWrit\""),
                unreadableWrite("w4", read, ", \"@type\": \"MemoryRead\""),
                unreadableWrite("w5", "5", ", \"@type\": \"MemoryRead\""),
                unreadableWrite("w6", "5", ", \"record_ref\": 7, \"store\": null"),
                unreadableWrite("w7", read, ", \"@type\": \"MemoryWrite\""));
        ArrayNode sources = Json.nodes().arrayNode();
        sources.addObject().put("id", SOURCE).put("class", "enforcement_point");
        Ingest.Result result = Ingest.ingest(Fixtures.writeIngestBundle(tempDir.resolve("b"), events, sources));
        Ingest.UnreadableWrite kept = new Ingest.UnreadableWrite("mem:r1", "kb");
        List<Ingest.UnreadableWrite> writes = new ArrayList<>();
        result.withheld.forEach(w -> writes.add(w.write()));
        assertEquals(
                java.util.Arrays.asList(
                        kept, kept, kept, null, null, new Ingest.UnreadableWrite(null, null), kept),
                writes);
        result.withheld.forEach(w -> assertEquals("spiffe://corp/agents/other", w.subject()));
    }
}
