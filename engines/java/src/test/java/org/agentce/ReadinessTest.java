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
import java.nio.file.attribute.PosixFilePermissions;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

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
        Files.writeString(dir.resolve("quarantine.jsonl"), "");
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

    private static final List<String> SHAPES =
            List.of("link into a mode-600 folder", "mode 000", "dangling link", "folder");

    /** Makes {@code dir/name} present but unreadable; returns a folder to reopen after the check. */
    private static Path makeUnreadable(Path dir, String name, String shape, Path tmp) throws IOException {
        Path target = dir.resolve(name);
        switch (shape) {
            case "link into a mode-600 folder" -> {
                Path locked = Files.createDirectories(tmp.resolve("locked-" + name));
                Files.move(target, locked.resolve(name));
                Files.createSymbolicLink(target, locked.resolve(name));
                Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("rw-------"));
                return locked;
            }
            case "mode 000" -> Files.setPosixFilePermissions(target, PosixFilePermissions.fromString("---------"));
            case "dangling link" -> {
                Files.delete(target);
                Files.createSymbolicLink(target, tmp.resolve("nowhere"));
            }
            default -> {
                Files.delete(target);
                Files.createDirectory(target);
            }
        }
        return null;
    }

    private static final Map<String, String> JSONL_RECORD = Map.of(
            "integrity.jsonl", "{\"stream\": \"s\", \"status\": \"verified\"}",
            "applicability.jsonl", "{\"drift\": []}",
            "quarantine.jsonl", "{\"record\": \"r\"}");

    /** The NOT READY reason for an unreadable report file; one outside the JSON profile names the rule and
     * line (SPEC 8.7, 18.184x), as Python's {@code _cannot_read}. */
    private static String cannotRead(String name, String rule) {
        return rule == null
                ? "cannot read report file " + name
                : "cannot read report file " + name + ": outside the JSON profile: " + rule + " at line 1 (SPEC 8.7)";
    }

    /** LF alone ends a line; any other separator, or a no-break space or byte-order mark, leaves a
     * line outside the JSON profile (18.184n), naming the rule as in Python and TypeScript (18.184x). */
    @ParameterizedTest
    @ValueSource(strings = {"integrity.jsonl", "applicability.jsonl", "quarantine.jsonl"})
    void jsonlLineOutsideTheLineRuleIsNotReady(String name, @TempDir Path tmp) throws IOException {
        String r = JSONL_RECORD.get(name);
        Map<String, String[]> cases = new LinkedHashMap<>();
        cases.put("joined VT", new String[] {r + "\u000b" + r + "\n", "extra_value"});
        for (String sep : List.of("\u000c", "\u0085", "\u2028", "\u2029", "\r")) {
            cases.put("joined " + (int) sep.charAt(0), new String[] {r + sep + r + "\n", "jsonl_line_split"});
        }
        cases.put("lone CR, no LF", new String[] {r + "\r" + r, "jsonl_line_split"});
        cases.put("leading NBSP", new String[] {"\u00a0" + r + "\n", "syntax"});
        cases.put("leading BOM", new String[] {"\ufeff" + r + "\n", "bom"});
        cases.put("trailing NBSP", new String[] {r + "\u00a0\n", "extra_value"});
        cases.put("NBSP-only line", new String[] {"\u00a0\n" + r + "\n", "syntax"});
        int i = 0;
        for (Map.Entry<String, String[]> c : cases.entrySet()) {
            Path dir = Files.createDirectories(tmp.resolve("c" + i++));
            writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
            Files.writeString(dir.resolve(name), c.getValue()[0]);
            assertEquals(List.of(cannotRead(name, c.getValue()[1])),
                    Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).reasons(), c.getKey());
        }
        Path dir = Files.createDirectories(tmp.resolve("not-utf8"));
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        Files.writeString(dir.resolve(name), r.substring(0, r.length() - 1) + ", \"x\": \"");
        Files.write(dir.resolve(name), new byte[] {(byte) 0xff, '"', '}', '\n'}, java.nio.file.StandardOpenOption.APPEND);
        assertEquals(List.of(cannotRead(name, "invalid_utf8")),
                Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).reasons(), "not UTF-8");
    }

    @ParameterizedTest
    @ValueSource(strings = {"integrity.jsonl", "applicability.jsonl", "quarantine.jsonl"})
    void jsonlLineInsideTheLineRuleStaysReady(String name, @TempDir Path tmp) throws IOException {
        String r = JSONL_RECORD.get(name);
        List<String> cases = List.of(
                r + "\r\n",
                "\t " + r + " \t\n",
                "\n \n\r\n" + r + "\n\n",
                r.replaceFirst(": ", ":\r") + "\n",
                r.substring(0, r.length() - 1) + ", \"x\": \"a b\"}\n",
                "");
        for (int i = 0; i < cases.size(); i++) {
            Path dir = Files.createDirectories(tmp.resolve("c" + i));
            writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
            Files.writeString(dir.resolve(name), cases.get(i));
            assertEquals(Readiness.READY, Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).verdict(),
                    cases.get(i));
        }
    }

    @Test
    void lastJsonlRecordWithoutLfIsRead(@TempDir Path tmp) throws IOException {
        Map<String, String[]> cases = Map.of(
                "integrity.jsonl", new String[] {"{\"stream\": \"s\", \"status\": \"failed\"}", "integrity failed on stream s"},
                "applicability.jsonl", new String[] {"{\"drift\": [{\"kind\": \"k\", \"ref\": \"r\"}]}", "applicability drift: k r"});
        for (Map.Entry<String, String[]> c : cases.entrySet()) {
            for (String tail : List.of("", "\r")) {
                Path dir = Files.createDirectories(tmp.resolve(c.getKey() + tail.length()));
                writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
                Files.writeString(dir.resolve(c.getKey()), "\n" + c.getValue()[0] + tail);
                assertEquals(List.of(c.getValue()[1]),
                        Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).reasons());
            }
        }
    }

    /** An entry that is there but cannot be read blocks; it never reads as absent, which would let a
     * recorded gap stop blocking (18.165, the same answer Python gives). */
    @ParameterizedTest
    @ValueSource(strings = {
            "coverage.json", "integrity.jsonl", "applicability.jsonl", "assertions.json", "quarantine.jsonl"})
    void unreadableReportFileIsNotReady(String name, @TempDir Path tmp) throws IOException {
        Assumptions.assumeFalse("root".equals(System.getProperty("user.name")), "running as root");
        for (String shape : SHAPES) {
            Path dir = Files.createDirectories(tmp.resolve(shape.replace(' ', '-')));
            writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
            Path locked = makeUnreadable(dir, name, shape, tmp);
            try {
                Readiness.Verdict v = Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of());
                assertEquals(Readiness.NOT_READY, v.verdict(), shape);
                assertEquals(List.of("cannot read report file " + name), v.reasons(), shape);
            } finally {
                if (locked != null) {
                    Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("rwx------"));
                }
            }
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "coverage.json", "integrity.jsonl", "applicability.jsonl", "assertions.json", "quarantine.jsonl"})
    void absentReportFileIsNotReadyAndNamed(String name, @TempDir Path dir) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        Files.delete(dir.resolve(name));
        Readiness.Verdict verdict = Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        assertEquals(List.of("missing report file " + name), verdict.reasons());
    }

    @Test
    void anAbsentReportFileOutweighsAGapRecordedAsALimitation(@TempDir Path dir) throws IOException {
        Path report = reportWithAssertions(
                dir, Json.nodes().arrayNode().add(assertion("OVS-03", "insufficient_evidence", "s")));
        Files.delete(report.resolve("coverage.json"));
        Readiness.Verdict verdict =
                Readiness.computeReadiness(report, SEVERITIES, List.of(), Set.of("OVS-03"));
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        assertTrue(verdict.reasons().contains("missing report file coverage.json"));
    }

    @Test
    void everyAbsentReportFileIsNamedSorted(@TempDir Path dir) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        List<String> names = List.of("applicability.jsonl", "assertions.json", "coverage.json", "integrity.jsonl");
        for (String name : names) {
            Files.delete(dir.resolve(name));
        }
        assertEquals(
                names.stream().map(n -> "missing report file " + n).toList(),
                Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).reasons());
    }

    @Test
    void reportFolderThatCannotBeEnteredIsNotReady(@TempDir Path tmp) throws IOException {
        Assumptions.assumeFalse("root".equals(System.getProperty("user.name")), "running as root");
        Path dir = Files.createDirectories(tmp.resolve("report"));
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        Files.setPosixFilePermissions(dir, PosixFilePermissions.fromString("rw-------"));
        try {
            Readiness.Verdict v = Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of());
            assertEquals(
                    List.of(
                            "cannot read report file applicability.jsonl",
                            "cannot read report file assertions.json",
                            "cannot read report file coverage.json",
                            "cannot read report file integrity.jsonl",
                            "cannot read report file quarantine.jsonl"),
                    v.reasons());
        } finally {
            Files.setPosixFilePermissions(dir, PosixFilePermissions.fromString("rwx------"));
        }
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
    void unknownCoverageIsNotReadyAndNamesTheSubject(@TempDir Path dir) throws IOException {
        // No independent denominator reconciles the subject's records (SPEC §6.5, §13.3.4).
        Readiness.Verdict verdict = Readiness.computeReadiness(
                reportWithCoverage(dir, coverageStatus("unknown")), SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        assertEquals(List.of("coverage unknown for subject s: no independent denominator"), verdict.reasons());
    }

    @Test
    void okCoverageAddsNoCoverageReason(@TempDir Path dir) throws IOException {
        Readiness.Verdict verdict = Readiness.computeReadiness(
                reportWithCoverage(dir, coverageStatus("ok")), SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.READY, verdict.verdict());
    }

    /** coverage.json with one subject {@code s} whose roll-up is {@code status}. */
    private static ObjectNode coverageStatus(String status) {
        ObjectNode coverage = Json.nodes().objectNode();
        coverage.putObject("subjects").putObject("s").put("coverage_status", status);
        return coverage;
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

    @Test
    void parseDateRejectsNonAsciiDigitsAndATrailingNewline() {
        // Verifier round 2, F1: this was already correct in Java (\d is ASCII-only, Matcher.matches()
        // requires the whole string) -- this pins that Python's own regression fix (re.ASCII plus
        // fullmatch) brought Python into agreement, not the other way around.
        assertNull(Readiness.parseDate("٢٠٢٦-٠١-٠١")); // Arabic-Indic digits
        assertNull(Readiness.parseDate("２０２６-０１-０１")); // fullwidth digits
        assertNull(Readiness.parseDate("2026-01-01\n")); // trailing newline
        assertNotNull(Readiness.parseDate("2026-01-01"));
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

    /** The register at {@code path} refused as outside the SPEC 8.7 YAML profile (18.184u). */
    private static void assertRegisterOutsideProfile(Path path, String rule, int line) {
        InputError e = assertThrows(InputError.class, () -> Readiness.loadDeviationRegister(path));
        assertEquals("input.deviation_invalid", e.key);
        assertTrue(e.reason.endsWith(" is outside the YAML profile: " + rule + " at line " + line + " (SPEC 8.7)."),
                e.reason);
        assertEquals(YamlProfile.HINTS.get(rule) + ".", e.fix);
    }

    @Test
    void yaml11BooleansAreOutsideTheYamlProfileQuotedStayStrings(@TempDir Path dir) throws IOException {
        assertRegisterOutsideProfile(writeYaml(
                dir,
                "deviations:\n  - control: OVS-03\n    rationale: no\n  - control: OVS-04\n    rationale: \"no\"\n"),
                "yaml11_bool", 3);
        Path quoted = writeYaml(dir, "deviations:\n  - control: OVS-04\n    rationale: \"no\"\n");
        assertEquals("no", Readiness.loadDeviationRegister(quoted).get(0).path("rationale").asText());
    }

    @Test
    void aMergeKeyIsOutsideTheYamlProfile(@TempDir Path dir) throws IOException {
        // Was: SnakeYAML's compose() never resolves `<<`, so Readiness merged at the node level (verifier round 2, F3).
        assertRegisterOutsideProfile(writeYaml(
                dir,
                "deviations:\n  - <<: &base\n      rationale: shared\n      owner: alice\n    control: OVS-03\n    owner: carol\n"),
                "merge_key", 2);
    }

    @Test
    void aDuplicateMappingKeyIsOutsideTheYamlProfile(@TempDir Path dir) throws IOException {
        // Was: the last value kept, as PyYAML keeps it (verifier round 2, F4).
        assertRegisterOutsideProfile(
                writeYaml(dir, "deviations:\n  - control: OVS-03\n    owner: alice\n    owner: carol\n"),
                "duplicate_key", 4);
    }

    @Test
    void aNanOrInfiniteFloatFieldNeverCrashesPyTruthyPyStrOrPyEquals(@TempDir Path dir) throws IOException {
        // Verifier round 2, F2: the B3 PyYAML-float-grammar port added `.nan`/`.inf` resolution, and
        // JsonNode.decimalValue() (BigDecimal) throws for both -- pyTruthy/pyStr/pyEquals must not
        // call it for a non-integral number. `rationale: .nan` is truthy (Python: bool(float('nan'))
        // is True), so the deviation is not flagged as missing that field.
        Path path = writeYaml(
                dir,
                "deviations:\n  - control: OVS-03\n    rationale: .nan\n    compensating_control: cc\n"
                        + "    owner: alice\n    approver: bob\n    granted: \"2026-01-01T00:00:00Z\"\n"
                        + "    expiry: \"2026-12-31T00:00:00Z\"\n");
        List<JsonNode> entries = Readiness.loadDeviationRegister(path);
        assertTrue(entries.get(0).path("rationale").isDouble());
        List<String> problems = Readiness.deviationLint(
                entries,
                Set.of("OVS-03"),
                Map.of(),
                Set.of(),
                null,
                Readiness.DEFAULT_MAX_DEVIATION_DAYS);
        assertTrue(problems.stream().noneMatch(r -> r.contains("missing rationale")));
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

    /** Not JSON of its shape reads NOT READY, never a crash and never READY (inbox row 393, 18.184h), and so
     * does an inner coverage entry or an applicability drift of the wrong shape (watch ruling 2026-10-21). */
    @ParameterizedTest
    @ValueSource(strings = {
        "coverage.json|null", "coverage.json|[]", "coverage.json|\"x\"", "coverage.json|3", "coverage.json|",
        "coverage.json|{", "coverage.json|{\"subjects\": []}", "coverage.json|{\"subjects\": null}",
        "coverage.json|{\"subjects\": {\"s\": 3}}", "coverage.json|{\"subjects\": {\"s\": null}}",
        "coverage.json|{\"subjects\": {\"s\": {\"event_types\": []}}}",
        "coverage.json|{\"subjects\": {\"s\": {\"event_types\": {\"t\": 3}}}}",
        "assertions.json|null", "assertions.json|{}", "assertions.json|", "assertions.json|[3]",
        "assertions.json|[null]", "integrity.jsonl|not json\n", "integrity.jsonl|3\n", "applicability.jsonl|[]\n",
        "applicability.jsonl|{\"drift\": {\"kind\": \"k\", \"ref\": \"r\"}}\n",
        "applicability.jsonl|{\"drift\": \"x\"}\n", "applicability.jsonl|{\"drift\": null}\n",
        "applicability.jsonl|{\"drift\": [3]}\n"})
    void aReportFileOfTheWrongShapeIsNotReady(String spec, @TempDir Path dir) throws IOException {
        String[] parts = spec.split("\\|", 2);
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        Files.writeString(dir.resolve(parts[0]), parts[1].replace("\\n", "\n"));
        Readiness.Verdict verdict = Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of());
        assertEquals(Readiness.NOT_READY, verdict.verdict());
        // Text that is not JSON at all is outside the JSON profile, which the reason names (18.184x).
        String body = parts[1].replace("\\n", "\n");
        boolean notJson = body.isEmpty() || body.equals("{") || body.equals("not json\n");
        assertEquals(List.of(cannotRead(parts[0], notJson ? "syntax" : null)), verdict.reasons());
    }

    @Test
    void aCoverageFileThatIsNotUtf8IsNotReady(@TempDir Path dir) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        byte[] text = "{\"subjects\": {}}".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        byte[] bytes = java.util.Arrays.copyOf(text, text.length + 1);
        bytes[text.length] = (byte) 0xff;
        Files.write(dir.resolve("coverage.json"), bytes);
        assertEquals(List.of(cannotRead("coverage.json", "invalid_utf8")),
                Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).reasons());
    }

    @Test
    void anEmptyCoverageObjectStaysReady(@TempDir Path dir) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), Json.nodes().objectNode(), List.of());
        assertEquals(Readiness.READY, Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).verdict());
    }

    /** A field the verdict is decided on that is not a JSON string (null included) makes its file unreadable,
     * with or without a deviation register, never read loosely (18.184m, as Python). */
    @ParameterizedTest
    @ValueSource(strings = {
        "integrity.jsonl|{\"status\": [\"broken\"], \"stream\": \"s\"}\n",
        "integrity.jsonl|{\"status\": null, \"stream\": \"s\"}\n",
        "assertions.json|[{\"control\": [\"DOC-01\"], \"outcome\": \"insufficient_evidence\"}]",
        "assertions.json|[{\"control\": \"DOC-01\", \"outcome\": 3}]",
        "assertions.json|[{\"control\": \"DOC-01\", \"window\": \"x\"}]",
        "assertions.json|[{\"control\": \"DOC-01\", \"window\": {\"end\": 3}}]",
        "assertions.json|[{\"control\": \"DOC-01\", \"deviation\": [\"D-1\"]}]",
        "coverage.json|{\"subjects\": {\"s\": {\"coverage_status\": [\"gap\"]}}}",
        "coverage.json|{\"subjects\": {\"s\": {\"event_types\": {\"t\": {\"status\": true}}}}}"})
    void aFieldDecidedOnOfTheWrongTypeIsNotReady(String spec, @TempDir Path dir) throws IOException {
        String[] parts = spec.split("\\|", 2);
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        Files.writeString(dir.resolve(parts[0]), parts[1]);
        for (List<JsonNode> deviations : List.of(List.<JsonNode>of(), List.<JsonNode>of(deviation()))) {
            Readiness.Verdict verdict = Readiness.computeReadiness(dir, SEVERITIES, deviations, Set.of());
            assertEquals(Readiness.NOT_READY, verdict.verdict());
            assertTrue(verdict.reasons().contains("cannot read report file " + parts[0]), verdict.reasons().toString());
        }
    }

    @Test
    void aNullDeviationStaysReadable(@TempDir Path dir) throws IOException {
        writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
        Files.writeString(dir.resolve("assertions.json"),
                "[{\"control\": \"DOC-01\", \"outcome\": \"conformant\", \"deviation\": null}]");
        assertEquals(Readiness.READY, Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).verdict());
    }

    /** One report-file rule in every engine (18.184q): the same table drives test_readiness.py and
     * readiness.test.ts. */
    @Test
    void reportFileRule(@TempDir Path tmp) throws IOException {
        for (ReportFileRuleCases.Case ruleCase : ReportFileRuleCases.all()) {
            Path dir = Files.createDirectories(tmp.resolve(ruleCase.id()));
            writeReport(dir, Json.nodes().arrayNode(), List.of(), defaultCoverage(), List.of());
            if (ruleCase.content() == null) {
                Files.delete(dir.resolve(ruleCase.file()));
            } else {
                Files.writeString(dir.resolve(ruleCase.file()), ruleCase.content());
            }
            assertEquals(ruleCase.readiness(),
                    Readiness.computeReadiness(dir, SEVERITIES, List.of(), Set.of()).reasons(), ruleCase.id());
        }
    }
}
