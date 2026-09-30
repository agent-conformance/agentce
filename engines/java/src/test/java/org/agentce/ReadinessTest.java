package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@code Readiness.java} against the Python reference's own fixture data ({@code
 * engines/python/tests/test_readiness.py}), copied verbatim where the case is portable, plus a set of
 * adversarial regressions this port must not reintroduce: duplicate reasons, non-BMP sort order,
 * missing-field {@code None} rendering, {@code pyRepr} backslash escaping, and the quoted-vs-unquoted
 * YAML timestamp distinction. Mirrors {@code readiness.test.ts}.
 */
class ReadinessTest {
    private static final Map<String, String> SEVERITIES = Map.of(
            "OVS-03", "high",
            "REC-01", "high",
            "DAT-01", "medium",
            "TRN-01", "medium");

    private static ObjectNode deviation() {
        ObjectNode node = Json.nodes().objectNode();
        node.put("control", "OVS-03");
        node.put("rationale", "compensated");
        node.put("compensating_control", "manual review");
        node.put("owner", "alice");
        node.put("approver", "bob");
        node.put("granted", "2026-01-01");
        node.put("expiry", "2026-03-01");
        return node;
    }

    private static ObjectNode defaultCoverage() {
        ObjectNode coverage = Json.nodes().objectNode();
        coverage.set("subjects", Json.nodes().objectNode());
        return coverage;
    }

    private static void writeReport(
            Path dir, ArrayNode assertions, List<ObjectNode> integrity, ObjectNode coverage, List<ObjectNode> applicability)
            throws IOException {
        Files.writeString(dir.resolve("assertions.json"), assertions.toString());
        StringBuilder integrityLines = new StringBuilder();
        for (ObjectNode r : integrity) {
            integrityLines.append(r).append("\n");
        }
        Files.writeString(dir.resolve("integrity.jsonl"), integrityLines.toString());
        Files.writeString(dir.resolve("coverage.json"), coverage.toString());
        StringBuilder applicabilityLines = new StringBuilder();
        for (ObjectNode s : applicability) {
            applicabilityLines.append(s).append("\n");
        }
        Files.writeString(dir.resolve("applicability.jsonl"), applicabilityLines.toString());
    }

    private static Path reportWithAssertions(Path dir, ArrayNode assertions) throws IOException {
        writeReport(dir, assertions, List.of(), defaultCoverage(), List.of());
        return dir;
    }

    private static Path reportWithIntegrity(Path dir, List<ObjectNode> integrity) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), integrity, defaultCoverage(), List.of());
        return dir;
    }

    private static Path reportWithCoverage(Path dir, ObjectNode coverage) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), coverage, List.of());
        return dir;
    }

    private static Path reportWithApplicability(Path dir, List<ObjectNode> applicability) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), applicability);
        return dir;
    }

    private static ObjectNode assertion(String control, String outcome, String subject) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("control", control);
        node.put("outcome", outcome);
        node.put("subject", subject);
        return node;
    }

    private static ObjectNode integrityRecord(String status, String stream) {
        ObjectNode node = Json.nodes().objectNode();
        if (status != null) {
            node.put("status", status);
        }
        if (stream != null) {
            node.put("stream", stream);
        }
        return node;
    }

    // --- compute_readiness (test_readiness.py) ------------------------------------------------------

    @Test
    void aCleanReportIsReady(@TempDir Path dir) throws IOException {
        ObjectNode coverage = Json.nodes().objectNode();
        ObjectNode subjects = Json.nodes().objectNode();
        ObjectNode s = Json.nodes().objectNode();
        s.put("coverage_status", "ok");
        subjects.set("s", s);
        coverage.set("subjects", subjects);
        writeReport(
                dir,
                Json.nodes().arrayNode().add(assertion("OVS-03", "conformant", "s")),
                List.of(integrityRecord("verified", "a")),
                coverage,
                List.of());

        Readiness.Verdict verdict = Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.READY, verdict.verdict());
        assertEquals(List.of(), verdict.reasons());
    }

    @Test
    void brokenIntegrityIsNotReady(@TempDir Path dir) throws IOException {
        Path report = reportWithIntegrity(dir, List.of(integrityRecord("failed", "gw")));
        assertEquals(Readiness.NOT_READY, Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of()).verdict());
    }

    @Test
    void aCoverageGapIsNotReady(@TempDir Path dir) throws IOException {
        ObjectNode coverage = Json.nodes().objectNode();
        ObjectNode subjects = Json.nodes().objectNode();
        ObjectNode s = Json.nodes().objectNode();
        s.put("coverage_status", "gap");
        subjects.set("s", s);
        coverage.set("subjects", subjects);
        Path report = reportWithCoverage(dir, coverage);
        Readiness.Verdict verdict = Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        assertTrue(verdict.reasons().stream().anyMatch(r -> r.contains("coverage")));
    }

    @Test
    void unknownCoverageIsNeverABlocker(@TempDir Path dir) throws IOException {
        ObjectNode coverage = Json.nodes().objectNode();
        ObjectNode subjects = Json.nodes().objectNode();
        ObjectNode s = Json.nodes().objectNode();
        s.put("coverage_status", "unknown");
        subjects.set("s", s);
        coverage.set("subjects", subjects);
        Path report = reportWithCoverage(dir, coverage);
        assertEquals(Readiness.READY, Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of()).verdict());
    }

    @Test
    void aBelowThresholdEventTypeIsNotReadyEvenUnderAnUnknownSubjectRollup(@TempDir Path dir) throws IOException {
        ObjectNode coverage = Json.nodes().objectNode();
        ObjectNode subjects = Json.nodes().objectNode();
        ObjectNode s = Json.nodes().objectNode();
        s.put("coverage_status", "unknown");
        ObjectNode eventTypes = Json.nodes().objectNode();
        ObjectNode toolCall = Json.nodes().objectNode();
        toolCall.put("status", "below_threshold");
        eventTypes.set("ToolCall", toolCall);
        s.set("event_types", eventTypes);
        subjects.set("s", s);
        coverage.set("subjects", subjects);
        Path report = reportWithCoverage(dir, coverage);
        Readiness.Verdict verdict = Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        assertTrue(verdict.reasons().stream().anyMatch(r -> r.contains("shortfall")));
    }

    @Test
    void applicabilityDriftIsNotReady(@TempDir Path dir) throws IOException {
        ObjectNode drift = Json.nodes().objectNode();
        drift.put("kind", "undeclared_decision_type");
        drift.put("ref", "dom:X");
        ObjectNode statement = Json.nodes().objectNode();
        statement.set("drift", Json.nodes().arrayNode().add(drift));
        Path report = reportWithApplicability(dir, List.of(statement));
        assertEquals(Readiness.NOT_READY, Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of()).verdict());
    }

    @Test
    void aHighSeverityInsufficientEvidenceWithNoGapRecordedIsNotReady(@TempDir Path dir) throws IOException {
        Path report = reportWithAssertions(
                dir, Json.nodes().arrayNode().add(assertion("OVS-03", "insufficient_evidence", "s")));
        assertEquals(Readiness.NOT_READY, Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of()).verdict());
    }

    @Test
    void aHighSeverityInsufficientEvidenceRecordedInGapsIsReadyWithLimitations(@TempDir Path dir) throws IOException {
        Path report = reportWithAssertions(
                dir, Json.nodes().arrayNode().add(assertion("OVS-03", "insufficient_evidence", "s")));
        Readiness.Verdict verdict =
                Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of("OVS-03"));
        assertEquals(Readiness.READY_WITH_LIMITATIONS, verdict.verdict());
        assertTrue(verdict.limitations().size() > 0);
    }

    @Test
    void aMediumSeverityInsufficientEvidenceIsReadyOutright(@TempDir Path dir) throws IOException {
        Path report = reportWithAssertions(
                dir, Json.nodes().arrayNode().add(assertion("DAT-01", "insufficient_evidence", "s")));
        assertEquals(Readiness.READY, Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of()).verdict());
    }

    // --- the round-2 regression scenario: duplicate reasons, non-BMP sort, missing fields ----------

    @Test
    void duplicateReasonsAreNeverDeduplicatedAndSortByByteCompareNotUtf16Order(@TempDir Path dir) throws IOException {
        // U+FF21 (fullwidth 'A') is a BMP character: its UTF-16 code unit and its UTF-8 leading byte
        // both exceed every ASCII byte, so a naive UTF-16 sort and byteCompare (UTF-8) *agree* on it --
        // it does NOT by itself distinguish the two orders (verifier round-1 finding: an earlier version
        // of this fixture claimed it did). U+1F600 ("grinning face"), an *astral* character represented
        // as a UTF-16 surrogate pair (leading code unit 0xD83D = 55357, below U+FF21's 0xFF21 = 65313)
        // but a 4-byte UTF-8 sequence (leading byte 0xF0 = 240, above U+FF21's leading byte 0xEF = 239),
        // is what actually reverses the two orders relative to each other. A record with a bad status but
        // no `stream` field at all -- Python's own `record.get("status") in _BAD_INTEGRITY` never enters
        // this branch for a record missing `status` entirely (`None` is not in the bad-status set), so
        // only a present-bad-status/absent-stream combination can ever exercise the `None`-rendering path
        // this fixture pins.
        Path report = reportWithIntegrity(
                dir,
                List.of(
                        integrityRecord("failed", "gw"),
                        integrityRecord("failed", "gw"),
                        integrityRecord("failed", "Ａ"),
                        integrityRecord("failed", "😀"),
                        integrityRecord("gap", null)));
        Readiness.Verdict verdict = Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of());
        long dupeCount = verdict.reasons().stream().filter(r -> r.equals("integrity failed on stream gw")).count();
        assertEquals(2, dupeCount);
        assertTrue(verdict.reasons().contains("integrity gap on stream None"));
        List<String> sorted = new java.util.ArrayList<>(verdict.reasons());
        sorted.sort(Json::byteCompare);
        assertEquals(sorted, verdict.reasons());
    }

    // --- deviation_lint (test_readiness.py) ---------------------------------------------------------

    @Test
    void aValidDeviationLintsClean() {
        List<String> problems = Readiness.deviationLint(
                List.of(deviation()),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertEquals(List.of(), problems);
    }

    @Test
    void aDeviationOnAnInsufficientEvidenceControlIsRefused() {
        List<String> problems = Readiness.deviationLint(
                List.of(deviation()),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("insufficient_evidence")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("insufficient_evidence")));
    }

    @Test
    void theIntFamilyCanNeverBeDeviated() {
        ObjectNode dev = deviation();
        dev.put("control", "INT-01");
        List<String> problems = Readiness.deviationLint(
                List.of(dev),
                Set.of("INT-01"),
                Map.of("INT-01", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("INT family")));
    }

    @Test
    void aSamePersonOwnerApproverAndAnEmptyButPresentFieldAreBothRefused() {
        ObjectNode dev = deviation();
        dev.put("approver", "alice");
        dev.put("rationale", "");
        List<String> problems = Readiness.deviationLint(
                List.of(dev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("distinct")));
        assertTrue(problems.stream().anyMatch(p -> p.contains("missing rationale")));
    }

    @Test
    void anEmptyArrayOrEmptyMappingFieldValueCountsAsMissingTooPyTruthy() {
        ObjectNode dev = deviation();
        dev.set("rationale", Json.nodes().arrayNode());
        dev.set("compensating_control", Json.nodes().objectNode());
        List<String> problems = Readiness.deviationLint(
                List.of(dev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("missing rationale")));
        assertTrue(problems.stream().anyMatch(p -> p.contains("missing compensating_control")));
    }

    @Test
    void aLifetimeOverTheMaxIsRefused() {
        ObjectNode dev = deviation();
        dev.put("expiry", "2027-01-01");
        List<String> problems = Readiness.deviationLint(
                List.of(dev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream()
                .anyMatch(p -> p.contains("lifetime exceeds " + Readiness.DEFAULT_MAX_DEVIATION_DAYS + " days")));
    }

    @Test
    void anUnknownControlIsRefused() {
        ObjectNode dev = deviation();
        dev.put("control", "ZZZ-99");
        List<String> problems = Readiness.deviationLint(
                List.of(dev), Set.of("OVS-03"), Map.of(), Set.of(), null, Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("not in the catalog")));
    }

    @Test
    void multiSubjectOutcomeAggregationIsOrderIndependent() {
        List<String> a = Readiness.deviationLint(
                List.of(deviation()),
                Set.of("OVS-03"),
                Map.of("OVS-03", new LinkedHashSet<>(List.of("non-conformant", "conformant"))),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        List<String> b = Readiness.deviationLint(
                List.of(deviation()),
                Set.of("OVS-03"),
                Map.of("OVS-03", new LinkedHashSet<>(List.of("conformant", "non-conformant"))),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertEquals(List.of(), a);
        assertEquals(List.of(), b);
    }

    @Test
    void anAlreadyAppliedControlSkipsTheOutcomeRecheck() {
        List<String> problems = Readiness.deviationLint(
                List.of(deviation()),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("partial")),
                Set.of("OVS-03"),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertEquals(List.of(), problems);
    }

    @Test
    void anAppliedAndExpiredDeviationIsRejected() {
        ObjectNode dev = deviation();
        dev.put("expiry", "2020-01-01");
        dev.put("granted", "2019-08-01");
        List<String> problems = Readiness.deviationLint(
                List.of(dev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("partial")),
                Set.of("OVS-03"),
                "2026-01-01",
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("applied deviation has expired")));
    }

    @Test
    void anUnexpiredOrNotYetAppliedEntryIsNeverRejectedOnExpiryAlone() {
        ObjectNode unexpiredDev = deviation();
        unexpiredDev.put("expiry", "2027-01-01");
        unexpiredDev.put("granted", "2026-11-01");
        List<String> unexpired = Readiness.deviationLint(
                List.of(unexpiredDev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("partial")),
                Set.of("OVS-03"),
                "2026-12-01",
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertFalse(unexpired.stream().anyMatch(p -> p.contains("expired")));

        ObjectNode notYetAppliedDev = deviation();
        notYetAppliedDev.put("expiry", "2020-01-01");
        List<String> notYetApplied = Readiness.deviationLint(
                List.of(notYetAppliedDev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                "2026-01-01",
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertFalse(notYetApplied.stream().anyMatch(p -> p.contains("expired")));
    }

    @Test
    void aDuplicateEntryForTheSameControlIsRejected() {
        List<String> problems = Readiness.deviationLint(
                List.of(deviation(), deviation()),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().anyMatch(p -> p.contains("duplicate deviation entry")));
    }

    @Test
    void anUnparseableExpiryOrGrantedIsRejectedNotSilentlySkipped() {
        ObjectNode badExpiryDev = deviation();
        badExpiryDev.put("expiry", "not-a-date");
        List<String> badExpiry = Readiness.deviationLint(
                List.of(badExpiryDev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(badExpiry.stream().anyMatch(p -> p.contains("expiry is not a valid RFC 3339 date")));

        ObjectNode badGrantedDev = deviation();
        badGrantedDev.put("granted", "not-a-date");
        List<String> badGranted = Readiness.deviationLint(
                List.of(badGrantedDev),
                Set.of("OVS-03"),
                Map.of("OVS-03", Set.of("non-conformant")),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(badGranted.stream().anyMatch(p -> p.contains("granted is not a valid RFC 3339 date")));
    }

    @Test
    void controlNullRendersAsNoneAnAbsentControlAsEmptyThePyGetVsAbsenceDistinction() {
        ObjectNode nullControlDev = deviation();
        nullControlDev.putNull("control");
        List<String> nullControl = Readiness.deviationLint(
                List.of(nullControlDev), Set.of("OVS-03"), Map.of(), Set.of(), null, Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(nullControl.stream().anyMatch(p -> p.startsWith("None:")));

        ObjectNode absentControlDev = deviation();
        absentControlDev.remove("control");
        List<String> absentControl = Readiness.deviationLint(
                List.of(absentControlDev),
                Set.of("OVS-03"),
                Map.of(),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(absentControl.stream().anyMatch(p -> p.startsWith("<none>:")));
    }

    // --- compute_readiness + deviations integration -------------------------------------------------

    @Test
    void anInvalidDeviationMakesTheWholeReportNotReady(@TempDir Path dir) throws IOException {
        Path report = reportWithAssertions(
                dir, Json.nodes().arrayNode().add(assertion("OVS-03", "non-conformant", "s")));
        ObjectNode dev = deviation();
        dev.put("expiry", "2027-06-01");
        Readiness.Verdict verdict = Readiness.computeReadiness(report, SEVERITIES, List.of(dev), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
    }

    @Test
    void aReportWhoseDeviationWasAlreadyAppliedIsAcceptedWithoutRecheckingOutcome(@TempDir Path dir) throws IOException {
        ObjectNode a = assertion("OVS-03", "partial", "s");
        a.put("deviation", "OVS-03");
        ObjectNode window = Json.nodes().objectNode();
        window.put("start", "2026-01-01");
        window.put("end", "2026-02-01");
        a.set("window", window);
        Path report = reportWithAssertions(dir, Json.nodes().arrayNode().add(a));
        Readiness.Verdict verdict = Readiness.computeReadiness(report, SEVERITIES, List.of(deviation()), Set.of());
        assertFalse(Readiness.NOT_READY.equals(verdict.verdict()));
    }

    @Test
    void anAppliedButSinceExpiredDeviationIsNotReadyExpiryRenderedViaPythonizeTimestamp(@TempDir Path dir)
            throws IOException {
        ObjectNode a = assertion("OVS-03", "partial", "s");
        a.put("deviation", "OVS-03");
        ObjectNode window = Json.nodes().objectNode();
        window.put("start", "2026-01-01");
        window.put("end", "2026-02-01");
        a.set("window", window);
        Path report = reportWithAssertions(dir, Json.nodes().arrayNode().add(a));

        Path regPath = dir.resolve("deviations.yaml");
        Files.writeString(
                regPath,
                String.join(
                        "\n",
                        "deviations:",
                        "  - control: OVS-03",
                        "    rationale: compensated",
                        "    compensating_control: manual review",
                        "    owner: alice",
                        "    approver: bob",
                        "    granted: 2019-08-01",
                        // Unquoted, so SnakeYAML resolves it as a timestamp and pythonizeTimestamp rewrites it.
                        "    expiry: 2020-01-01T10:00:00Z",
                        ""));
        Readiness.Verdict verdict =
                Readiness.computeReadiness(report, SEVERITIES, Readiness.loadDeviationRegister(regPath), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        assertTrue(verdict.reasons().stream().anyMatch(r -> r.contains("expiry 2020-01-01 10:00:00+00:00")));
    }

    // --- pythonizeTimestamp / parseDate: the round-2 regression fixtures ---------------------------

    @Test
    void pythonizeTimestampMatchesPythonsStrDateOrDatetimeGeneratedByRunningRealPython() {
        assertEquals("2021-01-01", Readiness.pythonizeTimestamp("2021-01-01"));
        assertEquals("2021-01-05", Readiness.pythonizeTimestamp("2021-1-5"));
        assertEquals("2021-01-01 00:00:00+00:00", Readiness.pythonizeTimestamp("2021-01-01T00:00:00Z"));
        assertEquals("2021-01-01 00:00:00.500000", Readiness.pythonizeTimestamp("2021-01-01T00:00:00.5"));
        assertEquals("2021-01-01 00:00:00", Readiness.pythonizeTimestamp("2021-01-01T00:00:00.000"));
        assertEquals("2021-01-01 10:00:00+05:00", Readiness.pythonizeTimestamp("2021-01-01T10:00:00+05:00"));
        assertEquals("2021-01-01 10:00:00", Readiness.pythonizeTimestamp("2021-01-01T10:00:00"));
    }

    @Test
    void parseDateRejectsAnOutOfRangeHourMinuteSecondOffsetNotOnlyAnInvalidCalendarDate() {
        assertNull(Readiness.parseDate("0000-01-01"));
        assertNull(Readiness.parseDate("2021-02-30"));
        assertNotNull(Readiness.parseDate("2020-02-29"));
        assertNull(Readiness.parseDate("2021-02-29"));
        assertNull(Readiness.parseDate("2021-01-01T25:00:00Z"));
        assertNull(Readiness.parseDate("2021-01-01T10:61:00Z"));
        assertNull(Readiness.parseDate("2021-01-01T23:59:60Z"));
        assertNull(Readiness.parseDate("2021-01-01T10:00:00+24:00"));
    }

    @Test
    void parseDateRejectsFromisoformatsExtraFormsRfc3339Only() {
        // TRADEOFFS.md/inbox row 19 (2026-09-30 maintainer decision): one grammar in all three
        // engines. Python's own parse_date now rejects these same four forms (cross-engine vector)
        // even though real datetime.fromisoformat still accepts each of them.
        assertNull(Readiness.parseDate("20211231")); // basic format (no separators)
        assertNull(Readiness.parseDate("2021-W52-5")); // ISO week date
        assertNull(Readiness.parseDate("2021-12-31T10")); // hour-only precision, no minutes/seconds
        assertNull(Readiness.parseDate("2021-12-31T10:30")); // minute precision, no seconds
        assertNotNull(Readiness.parseDate("2021-12-31T10:30:00")); // RFC 3339 still accepted
    }

    // --- loadDeviationRegister: PyYAML-matching implicit resolution --------------------------------

    private static Path writeYaml(Path dir, String contents) throws IOException {
        Path path = dir.resolve("deviations.yaml");
        Files.writeString(path, contents);
        return path;
    }

    @Test
    void aQuotedTimestampScalarIsNeverRewrittenByteIdenticalToTheSourceText(@TempDir Path dir) throws IOException {
        Path path = writeYaml(dir, "deviations:\n  - control: \"OVS-03\"\n    expiry: \"2021-01-01T00:00:00Z\"\n");
        List<JsonNode> entries = Readiness.loadDeviationRegister(path);
        assertEquals("2021-01-01T00:00:00Z", entries.get(0).path("expiry").asText());
    }

    @Test
    void anUnquotedTimestampScalarIsPythonizedMatchingPyyamlsStrDatetimeRendering(@TempDir Path dir) throws IOException {
        Path path = writeYaml(dir, "deviations:\n  - control: OVS-03\n    expiry: 2021-01-01T00:00:00Z\n");
        List<JsonNode> entries = Readiness.loadDeviationRegister(path);
        assertEquals("2021-01-01 00:00:00+00:00", entries.get(0).path("expiry").asText());
    }

    @Test
    void aColonlessOffsetScalarIsNeverResolvedAsATimestampStaysAPlainString(@TempDir Path dir) throws IOException {
        Path path = writeYaml(dir, "deviations:\n  - control: OVS-03\n    expiry: 2021-01-01T10:00:00+0500\n");
        List<JsonNode> entries = Readiness.loadDeviationRegister(path);
        assertEquals("2021-01-01T10:00:00+0500", entries.get(0).path("expiry").asText());
    }

    @Test
    void yaml11BooleansResolveUnquotedStayStringsQuoted(@TempDir Path dir) throws IOException {
        Path path = writeYaml(
                dir,
                "deviations:\n  - control: OVS-03\n    rationale: no\n  - control: OVS-04\n    rationale: \"no\"\n");
        List<JsonNode> entries = Readiness.loadDeviationRegister(path);
        assertFalse(entries.get(0).path("rationale").asBoolean(true));
        assertTrue(entries.get(0).path("rationale").isBoolean());
        assertEquals("no", entries.get(1).path("rationale").asText());
    }

    @Test
    void aPythonFalsyTopLevelValueIsTreatedAsNoDeviationsNotAShapeError(@TempDir Path dir) throws IOException {
        Path path = writeYaml(dir, "false\n");
        assertEquals(List.of(), Readiness.loadDeviationRegister(path));
    }

    @Test
    void deviationsNullIsAShapeErrorDistinctFromAnAbsentDeviationsKey(@TempDir Path dir) throws IOException {
        Path nullDeviations = writeYaml(dir, "deviations: null\n");
        InputError error = assertThrows(InputError.class, () -> Readiness.loadDeviationRegister(nullDeviations));
        assertEquals("input.deviation_invalid", error.key);
        assertTrue(error.reason.contains("deviations"));
        assertTrue(error.reason.contains("key is not a list"));

        Path dir2 = Files.createTempDirectory("agentce-readiness-yaml-");
        try {
            Path absentKey = writeYaml(dir2, "other: 1\n");
            assertEquals(List.of(), Readiness.loadDeviationRegister(absentKey));
        } finally {
            Files.deleteIfExists(dir2.resolve("deviations.yaml"));
            Files.deleteIfExists(dir2);
        }
    }

    @Test
    void aYamlSyntaxErrorMapsToInputDeviationInvalidNeverACrash(@TempDir Path dir) throws IOException {
        Path path = writeYaml(dir, "deviations: [\n");
        InputError error = assertThrows(InputError.class, () -> Readiness.loadDeviationRegister(path));
        assertEquals("input.deviation_invalid", error.key);
    }

    // --- parseGapsFile: Python's Unicode-aware \b -----------------------------------------------

    @Test
    void parseGapsFileMatchesPythonsUnicodeAwareWordBoundaryNotJssAsciiOnlyDefault() {
        // An accented-letter prefix, a leading em-dash, and a trailing non-ASCII digit: Python's
        // re.findall gives exactly 1 match (AB-12), never 3 -- confirmed against real Python this item.
        Set<String> matches = Readiness.parseGapsFile("éAB-12 —CD-34 EF-56٠");
        assertEquals(Set.of("CD-34"), matches);
    }

    @Test
    void parseGapsFileDeduplicatesMatchingSetOfReFindall() {
        Set<String> matches = Readiness.parseGapsFile("OVS-03 owned by alice on 2026-02-01; also OVS-03");
        assertEquals(Set.of("OVS-03"), matches);
    }

    // --- normalizeDeviationDates -----------------------------------------------------------------

    @Test
    void normalizeDeviationDatesPassesAPlainStringNumberOrBooleanThroughUnchanged() {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("control", "OVS-03");
        entry.put("granted", "already a string");
        entry.put("count", 3);
        entry.put("ok", true);
        List<Map<String, Object>> out = Readiness.normalizeDeviationDates(List.of(entry));
        assertEquals(List.of(entry), out);
    }
}
