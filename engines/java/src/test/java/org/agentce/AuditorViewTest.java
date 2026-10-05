package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * The auditor view (18.17a) is a port of {@code agentce/auditor_view.py}: these tests pin the
 * selection's order, the {@code by_clause} index, the manual note and the deviation detail.
 * Byte-identity with Python's own output over a real run is VG-DEVIATIONS-PARITY's job (the
 * {@code auditor-view} seam).
 */
class AuditorViewTest {
    private static Assertions.Assertion assertion(String control, String subject) {
        Assertions.Assertion a = new Assertions.Assertion();
        a.control = control;
        a.controlVersion = "2026.09";
        a.subject = subject;
        a.outcome = "conformant";
        a.rung = 2;
        a.mode = "automated";
        a.window = new String[] {"2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"};
        a.population = new int[] {1, 0};
        a.severity = "high";
        a.family = control.substring(0, 3);
        return a;
    }

    private static Assertions.Assertion withCrosswalk(Assertions.Assertion a, String... frameworkClause) {
        for (int i = 0; i < frameworkClause.length; i += 2) {
            ObjectNode xw = Json.nodes().objectNode();
            xw.put("framework", frameworkClause[i]);
            xw.put("clause", frameworkClause[i + 1]);
            a.crosswalk.add(xw);
        }
        return a;
    }

    private static List<String> field(ObjectNode view, String name) {
        List<String> out = new ArrayList<>();
        for (JsonNode clause : view.get("clauses")) {
            out.add(clause.has(name) ? clause.get(name).toString() : null);
        }
        return out;
    }

    @Test
    void computeAuditorViewSortsClausesByControlThenSubject() {
        ObjectNode view = AuditorView.computeAuditorView(
                List.of(
                        assertion("AUV-02", "spiffe://b"),
                        assertion("AUV-01", "spiffe://b"),
                        assertion("AUV-02", "spiffe://a"),
                        assertion("AUV-01", "spiffe://a")),
                null);
        List<String> order = new ArrayList<>();
        for (JsonNode c : view.get("clauses")) {
            order.add(c.get("control").asText() + " " + c.get("subject").asText());
        }
        assertEquals(List.of("AUV-01 spiffe://a", "AUV-01 spiffe://b", "AUV-02 spiffe://a", "AUV-02 spiffe://b"), order);
    }

    @Test
    void computeAuditorViewIndexesByClauseDedupedAndSorted() {
        ObjectNode view = AuditorView.computeAuditorView(
                List.of(
                        withCrosswalk(assertion("AUV-02", "spiffe://a"), "iso", "9.1", "eu", "Art.12"),
                        withCrosswalk(assertion("AUV-01", "spiffe://a"), "eu", "Art.12"),
                        withCrosswalk(assertion("AUV-01", "spiffe://b"), "eu", "Art.12", "eu", "Art.10")),
                null);
        assertEquals(
                "{\"eu\":{\"Art.10\":[\"AUV-01\"],\"Art.12\":[\"AUV-01\",\"AUV-02\"]},\"iso\":{\"9.1\":[\"AUV-02\"]}}",
                view.get("by_clause").toString());
    }

    @Test
    void computeAuditorViewNotesOnlyNotAssessedManualControls() {
        Assertions.Assertion manual = assertion("AUV-01", "spiffe://a");
        manual.mode = "manual";
        manual.outcome = "not_assessed";
        Assertions.Assertion semi = assertion("AUV-02", "spiffe://a");
        semi.mode = "semi-automated";
        semi.outcome = "not_assessed";
        Assertions.Assertion manualDone = assertion("AUV-03", "spiffe://a");
        manualDone.mode = "manual";
        Assertions.Assertion automated = assertion("AUV-04", "spiffe://a");
        automated.outcome = "not_assessed";
        ObjectNode view = AuditorView.computeAuditorView(List.of(manual, semi, manualDone, automated), null);
        String note = Json.nodes().textNode(Messages.catalogue().get("report.manual_checklist_not_yet_evaluated")).toString();
        List<String> expected = new ArrayList<>(List.of(note, note));
        expected.add(null);
        expected.add(null);
        assertEquals(expected, field(view, "manual_checklist_note"));
    }

    @Test
    void computeAuditorViewCopiesTheRegisterEntryVerbatim() {
        ObjectNode entry = Json.nodes().objectNode();
        entry.put("control", "AUV-01");
        entry.put("rationale", "Accepted pending remediation. <script>alert(1)</script>");
        entry.put("compensating_control", "Manual quarterly review.");
        entry.put("owner", "user:ops-lead@example.com");
        entry.put("approver", "user:ciso@example.com");
        entry.put("granted", "2026-01-01T00:00:00.000Z");
        entry.put("expiry", "2026-06-01T00:00:00.000Z");
        entry.putArray("evidence_refs").add("reference/plan.md");
        Assertions.Assertion a = assertion("AUV-01", "spiffe://a");
        a.outcome = "partial";
        a.deviation = "AUV-01";
        ObjectNode view = AuditorView.computeAuditorView(List.of(a), List.of(entry));
        ObjectNode expected = entry.deepCopy();
        expected.remove("control");
        assertEquals(expected, view.get("clauses").get(0).get("deviation"));
    }

    @Test
    void computeAuditorViewWithoutARegisterNamesOnlyTheControl() {
        Assertions.Assertion a = assertion("AUV-01", "spiffe://a");
        a.outcome = "partial";
        a.deviation = "AUV-01";
        ObjectNode view = AuditorView.computeAuditorView(List.of(a), null);
        assertEquals("{\"control\":\"AUV-01\"}", view.get("clauses").get(0).get("deviation").toString());
        assertFalse(view.get("clauses").get(0).get("deviation").has("rationale"));
    }

    @Test
    void computeAuditorViewCountsUnlessGivenCounts() {
        Assertions.Assertion failed = assertion("AUV-02", "spiffe://a");
        failed.outcome = "non-conformant";
        List<Assertions.Assertion> assertions = List.of(assertion("AUV-01", "spiffe://a"), failed);
        JsonNode own = AuditorView.computeAuditorView(assertions, null).get("counts");
        assertEquals(1, own.get("conformant").asInt());
        assertEquals(1, own.get("non-conformant").asInt());
        assertEquals("{\"conformant\":7}",
                AuditorView.computeAuditorView(assertions, null, Map.of("conformant", 7)).get("counts").toString());
    }
}
