package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * The auditor view (18.17): "can I rely on this, clause by clause?"
 *
 * <p>A faithful port of the Python reference ({@code agentce/auditor_view.py}). {@link
 * #computeAuditorView} adds no new rollup and re-derives no verdict: one {@link Assertions.Assertion}
 * already is one clause's full record, so {@code clauses} selects that record as-is, one entry per
 * assertion, sorted by (control, subject). {@code by_clause} is a navigation index built from the same
 * entries' own {@code crosswalk} field, sorted by (framework, clause) with its control ids deduplicated
 * and sorted. A manual or semi-automated control still {@code not_assessed} carries the disclosed
 * not-yet-evaluated note instead of a fabricated result.
 */
public final class AuditorView {
    private AuditorView() {}

    /** Modes whose {@code not_assessed} outcome gets the disclosed not-yet-evaluated note. */
    private static final Set<String> MANUAL_MODES = Set.of("manual", "semi-automated");

    /** The register record's six required fields (deviation-register.schema.json), copied verbatim. */
    private static final List<String> DEVIATION_FIELDS =
            List.of("rationale", "compensating_control", "owner", "approver", "granted", "expiry");

    /** The matching register entry for {@code deviation} (an assertion's own {@code deviation} field),
     * verbatim, or, when no register was passed (a report re-rendered from {@code assertions.json}
     * alone), a minimal record naming only the control id. Never re-validated here: the register was
     * already linted. */
    private static ObjectNode deviationDetail(String deviation, Map<String, JsonNode> byControl) {
        ObjectNode detail = Json.nodes().objectNode();
        JsonNode entry = byControl.get(deviation);
        if (entry == null) {
            detail.put("control", deviation);
            return detail;
        }
        for (String field : DEVIATION_FIELDS) {
            detail.put(field, Assess.fieldStr(entry, field));
        }
        JsonNode refs = entry.get("evidence_refs");
        if (refs != null && Readiness.pyTruthy(refs)) {
            ArrayNode out = detail.putArray("evidence_refs");
            if (refs.isTextual()) {
                refs.textValue().codePoints().forEach(cp -> out.add(new String(Character.toChars(cp))));
            } else {
                refs.forEach(ref -> out.add(Readiness.pyStr(ref)));
            }
        }
        return detail;
    }

    /** {@link #computeAuditorView} with {@code counts} aggregated from {@code assertions}. */
    public static ObjectNode computeAuditorView(List<Assertions.Assertion> assertions, List<JsonNode> deviations) {
        return computeAuditorView(assertions, deviations, null);
    }

    /** Return the clause-by-clause selection of {@code assertions} for a whole run. Deterministic: no
     * clock, no locale, no dependence on the input order. */
    public static ObjectNode computeAuditorView(
            List<Assertions.Assertion> assertions, List<JsonNode> deviations, Map<String, Integer> counts) {
        Map<String, JsonNode> byControl = Assess.deviationsByControl(deviations);
        String note = Messages.catalogue().get("report.manual_checklist_not_yet_evaluated");

        ObjectNode view = Json.nodes().objectNode();
        ArrayNode clauses = view.putArray("clauses");
        Comparator<String> bytes = Json::byteCompare;
        Map<String, Map<String, Set<String>>> byClause = new TreeMap<>(bytes);
        List<Assertions.Assertion> ordered = new ArrayList<>(assertions);
        ordered.sort(Comparator.<Assertions.Assertion, String>comparing(a -> a.control, bytes)
                .thenComparing(a -> a.subject, bytes));
        for (Assertions.Assertion a : ordered) {
            ObjectNode entry = clauses.addObject();
            entry.put("control", a.control);
            entry.put("control_version", a.controlVersion);
            entry.put("subject", a.subject);
            entry.put("outcome", a.outcome);
            entry.put("mode", a.mode);
            entry.put("rung", a.rung);
            ArrayNode evidence = entry.putArray("evidence");
            a.evidence.forEach(e -> evidence.add(e.toJson()));
            entry.putArray("crosswalk").addAll(a.crosswalk);
            if (a.deviation != null && !a.deviation.isEmpty()) {
                entry.set("deviation", deviationDetail(a.deviation, byControl));
            }
            if (MANUAL_MODES.contains(a.mode) && "not_assessed".equals(a.outcome)) {
                entry.put("manual_checklist_note", note);
            }
            for (JsonNode xw : a.crosswalk) {
                byClause.computeIfAbsent(Assess.fieldStr(xw, "framework"), k -> new TreeMap<>(bytes))
                        .computeIfAbsent(Assess.fieldStr(xw, "clause"), k -> new TreeSet<>(bytes))
                        .add(a.control);
            }
        }

        ObjectNode byClauseOut = view.putObject("by_clause");
        byClause.forEach((framework, clauseMap) -> {
            ObjectNode out = byClauseOut.putObject(framework);
            clauseMap.forEach((clause, ids) -> ids.forEach(out.putArray(clause)::add));
        });
        ObjectNode countsOut = view.putObject("counts");
        (counts != null ? counts : Assertions.aggregate(assertions)).forEach(countsOut::put);
        return view;
    }
}
