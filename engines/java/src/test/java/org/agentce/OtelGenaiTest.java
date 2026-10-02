package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

/**
 * The Java otel-genai port matches the Python reference on every fixture and hostile vector (18.29,
 * contract {@code P18-18.29} C2) -- a faithful port of the TypeScript engine's {@code
 * otelGenai.test.ts}, driving the same fixture and hostile-vector directories, same assertions.
 *
 * <p>Comparisons go through {@link Json#pretty} rather than {@link JsonNode#equals}: the parser's
 * {@code USE_BIG_INTEGER_FOR_INTS} setting means every integer literal in a parsed {@code
 * expected.jsonl} is a {@code BigIntegerNode}, while a field built with {@code ObjectNode.put(String,
 * int)} is an {@code IntNode} -- different subtypes {@code equals()} would never consider equal even
 * for the same value. Serialised text sidesteps that entirely.
 */
class OtelGenaiTest {

    private static final Path FIXTURES_DIR = TestPaths.repoRoot().resolve("adapters/otel-genai/fixtures");
    private static final Path HOSTILE_DIR =
            TestPaths.repoRoot().resolve("spec/model/test-vectors/otel-genai-hostile");

    private static JsonNode loadAdaptArgs(Path dir) {
        return Json.parseFile(dir.resolve("adapt.json"));
    }

    private static OtelGenai.AdaptResult runAdapt(Path dir) {
        try {
            byte[] bytes = Files.readAllBytes(dir.resolve("input.json"));
            JsonNode args = loadAdaptArgs(dir);
            String subject = args.has("subject") ? args.get("subject").asText() : null;
            String sourceClass = args.has("source_class") && !args.get("source_class").isNull()
                    ? args.get("source_class").asText()
                    : null;
            String source =
                    args.has("source") && !args.get("source").isNull() ? args.get("source").asText() : null;
            return OtelGenai.adapt(bytes, subject, sourceClass, source);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    private static ArrayNode readExpectedEvents(Path dir) {
        try {
            ArrayNode out = Json.nodes().arrayNode();
            for (String line : Files.readAllLines(dir.resolve("expected.jsonl"), StandardCharsets.UTF_8)) {
                if (!line.isBlank()) {
                    out.add(Json.parse(line));
                }
            }
            return out;
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    private static String prettyEvents(List<JsonNode> events) {
        ArrayNode arr = Json.nodes().arrayNode();
        arr.addAll(events);
        return Json.pretty(arr);
    }

    private static ObjectNode reportAsJson(OtelGenai.AdapterReport report) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("adapter", report.adapter());
        ArrayNode conventions = node.putArray("conventions");
        for (String c : report.conventions()) {
            conventions.add(c);
        }
        node.put("spans_seen", report.spansSeen());
        node.put("events_emitted", report.eventsEmitted());
        ArrayNode skipped = node.putArray("skipped");
        for (OtelGenai.SkippedSpan s : report.skipped()) {
            ObjectNode sNode = skipped.addObject();
            sNode.put("name", s.name());
            sNode.put("reason", s.reason());
            sNode.put("span_id", s.spanId());
        }
        return node;
    }

    private static void assertEventsMatch(Path dir, OtelGenai.AdaptResult result) {
        assertEquals(Json.pretty(readExpectedEvents(dir)), prettyEvents(result.events()));
    }

    private static List<Path> listDirs(Path parent) {
        try (Stream<Path> paths = Files.list(parent)) {
            return paths.filter(Files::isDirectory).sorted().toList();
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    @Test
    void theVendorFixtureSetIsPresent() {
        assertTrue(listDirs(FIXTURES_DIR).size() >= 9);
    }

    @TestFactory
    Stream<DynamicTest> vendorFixtures() {
        return listDirs(FIXTURES_DIR).stream()
                .map(dir -> DynamicTest.dynamicTest("vendor fixture " + dir.getFileName(), () -> {
                    assertEventsMatch(dir, runAdapt(dir));
                }));
    }

    @Test
    void theHostileVectorSetIsPresent() {
        assertTrue(listDirs(HOSTILE_DIR).size() >= 20);
    }

    @TestFactory
    Stream<DynamicTest> hostileVectors() {
        return listDirs(HOSTILE_DIR).stream().map(dir -> DynamicTest.dynamicTest(
                "hostile vector " + dir.getFileName(),
                () -> {
                    Path errorPath = dir.resolve("expected-error.json");
                    if (Files.exists(errorPath)) {
                        String wantReason = Json.parseFile(errorPath).get("reason").asText();
                        if (wantReason.startsWith("canonical:")) {
                            String canonReason = wantReason.substring("canonical:".length());
                            OtelGenai.AdaptResult result = runAdapt(dir);
                            boolean threw = false;
                            for (JsonNode event : result.events()) {
                                try {
                                    Canonical.canonicalString(event);
                                } catch (Canonical.CanonicalizationError exc) {
                                    assertEquals(canonReason, exc.reason);
                                    threw = true;
                                }
                            }
                            assertTrue(threw, "expected at least one event to fail canonicalization");
                            return;
                        }
                        try {
                            runAdapt(dir);
                            fail("expected " + dir.getFileName() + " to raise " + wantReason);
                        } catch (OtelGenai.OtelGenaiAdapterError exc) {
                            assertEquals(wantReason, exc.reason);
                        }
                        return;
                    }
                    OtelGenai.AdaptResult result = runAdapt(dir);
                    assertEventsMatch(dir, result);
                    Path reportPath = dir.resolve("expected-report.json");
                    if (Files.exists(reportPath)) {
                        assertEquals(
                                Json.pretty(Json.parseFile(reportPath)), Json.pretty(reportAsJson(result.report())));
                    }
                }));
    }

    /** A minimal OTLP/JSON document with one span carrying a single {@code startTimeUnixNano}. */
    private static byte[] docWithTimestamp(String unixNanos) {
        String doc = "{\"resourceSpans\":[{\"resource\":{\"attributes\":"
                + "[{\"key\":\"service.name\",\"value\":{\"stringValue\":\"svc\"}}]},"
                + "\"scopeSpans\":[{\"scope\":{\"name\":\"x\"},"
                + "\"schemaUrl\":\"https://opentelemetry.io/schemas/1.30.0\","
                + "\"spans\":[{\"traceId\":\"t\",\"spanId\":\"s\",\"name\":\"chat x\","
                + "\"startTimeUnixNano\":\"" + unixNanos + "\","
                + "\"attributes\":[{\"key\":\"gen_ai.operation.name\",\"value\":{\"stringValue\":\"chat\"}}]}]}]}]}";
        return doc.getBytes(StandardCharsets.UTF_8);
    }

    @Test
    void timestampTruncationBoundary() {
        OtelGenai.AdaptResult kept = OtelGenai.adapt(docWithTimestamp("253402300799000000000"), "s", null, null);
        assertEquals(1, kept.events().size());
        assertEquals("9999-12-31T23:59:59.000Z", kept.events().get(0).get("time").asText());

        OtelGenai.AdaptResult dropped = OtelGenai.adapt(docWithTimestamp("253402300800000000000"), "s", null, null);
        assertEquals(0, dropped.events().size());
        assertEquals(1, dropped.report().skipped().size());
        // The pre-existing `unrecognised_operation`-not-`missing_operation` mislabeling (ported
        // byte-for-byte from the reference, MAINTAINER-INBOX row 48): a recognised operation with
        // no usable timestamp is skipped under the same reason a truly unrecognised operation would
        // be.
        assertEquals("unrecognised_operation", dropped.report().skipped().get(0).reason());
    }

    @Test
    void timestampFormattingIsClockAndLocaleIndependent() {
        TimeZone previousTz = TimeZone.getDefault();
        Locale previousLocale = Locale.getDefault();
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Kiritimati")); // UTC+14
            Locale.setDefault(Locale.forLanguageTag("ar-SA")); // Eastern Arabic-Indic digits hazard
            OtelGenai.AdaptResult result = OtelGenai.adapt(docWithTimestamp("1734000000123000000"), "s", null, null);
            assertEquals(1, result.events().size());
            assertEquals("2024-12-12T10:40:00.123Z", result.events().get(0).get("time").asText());
        } finally {
            TimeZone.setDefault(previousTz);
            Locale.setDefault(previousLocale);
        }
    }
}
