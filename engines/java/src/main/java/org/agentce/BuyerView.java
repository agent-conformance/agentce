package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The buyer view (18.18): generated questionnaire answers, a one-page summary, and how to check the
 * report.
 *
 * <p>A faithful port of the Python reference ({@code agentce/buyer_view.py}). {@link #computeBuyerView}
 * adds no new rollup and re-derives no verdict (SPEC §9.2): a CAIQ or AI Controls Matrix question is
 * answered by selecting the {@code Assertion.crosswalk} entries whose {@code framework} is one of the two
 * questionnaire frameworks. Field names, key order and sort order match Python's so {@code buyer.json}
 * is byte-identical across engines.
 */
public final class BuyerView {
    private BuyerView() {}

    /** The two questionnaire frameworks this view answers, in fixed render order. */
    public static final List<String> BUYER_FRAMEWORKS = List.of("caiq", "ai-controls-matrix");

    /** Each framework's own {@code version:} field (buyer_view.py's {@code BUYER_QUESTIONNAIRE_VERSIONS}). */
    public static final Map<String, String> BUYER_QUESTIONNAIRE_VERSIONS =
            Map.of("caiq", "4.0.2", "ai-controls-matrix", "1.1");

    /** Each framework's display title for the "evidence against {title} {version}" heading. */
    public static final Map<String, String> BUYER_QUESTIONNAIRE_TITLES =
            Map.of("caiq", "the CAIQ", "ai-controls-matrix", "the AI Controls Matrix");

    /** Modes whose {@code not_assessed} outcome gets the disclosed not-yet-evaluated note. */
    private static final Set<String> MANUAL_MODES = Set.of("manual", "semi-automated");

    private static final List<String> SORT_FIELDS =
            List.of("framework", "question", "control", "control_version", "subject", "outcome");

    /** A check-ref's (subject, control, control_version) triple (buyer_view.py's {@code _ref_key};
     * {@code catalog} is not part of it, the limitation Python discloses). */
    private static List<String> refKey(String subject, String control, String controlVersion) {
        return List.of(subject, control, controlVersion);
    }

    private static List<String> refKey(JsonNode ref) {
        return refKey(ref.get("subject").asText(), ref.get("control").asText(), ref.get("control_version").asText());
    }

    private static Iterable<JsonNode> bucket(JsonNode blindSpots, String name) {
        JsonNode node = blindSpots.get(name);
        return node != null ? node : List.of();
    }

    /** The triple -> gap-step index, built once per run (buyer_view.py's {@code _buyer_gap_step_index}):
     * a blind spot's missing requirements first, then {@code no_population} and {@code coverage_unknown}
     * where no earlier bucket matched. */
    private static Map<List<String>, ObjectNode> gapStepIndex(JsonNode blindSpots) {
        Map<List<String>, ArrayNode> missingByKey = new LinkedHashMap<>();
        for (JsonNode bs : bucket(blindSpots, "blind_spots")) {
            ObjectNode entry = Json.nodes().objectNode();
            for (String field : List.of("event", "class", "ladder_rung", "owner_key", "step_kind")) {
                entry.set(field, bs.get(field));
            }
            List<JsonNode> refs = new ArrayList<>();
            bs.get("unlocked_checks").forEach(refs::add);
            bs.get("needed_by_checks").forEach(refs::add);
            for (JsonNode ref : refs) {
                missingByKey.computeIfAbsent(refKey(ref), k -> Json.nodes().arrayNode()).add(entry);
            }
        }
        Map<List<String>, ObjectNode> index = new HashMap<>();
        missingByKey.forEach((key, missing) -> {
            ObjectNode step = Json.nodes().objectNode();
            step.put("kind", "blind_spot");
            step.set("missing", missing);
            index.put(key, step);
        });
        for (String kind : List.of("no_population", "coverage_unknown")) {
            for (JsonNode ref : bucket(blindSpots, kind)) {
                index.computeIfAbsent(refKey(ref), k -> Json.nodes().objectNode().put("kind", kind));
            }
        }
        return index;
    }

    /** {@link #computeBuyerView} with {@code counts} aggregated from {@code assertions}. */
    public static ObjectNode computeBuyerView(List<Assertions.Assertion> assertions, JsonNode blindSpots) {
        return computeBuyerView(assertions, blindSpots, null);
    }

    /** The questionnaire-answer selection of {@code assertions} for a whole run (buyer_view.py's
     * {@code compute_buyer_view}): {@code answers} sorted by (framework, question, control,
     * control_version, subject, outcome) in code-point order, {@code by_question} one map per framework. */
    public static ObjectNode computeBuyerView(
            List<Assertions.Assertion> assertions, JsonNode blindSpots, Map<String, Integer> counts) {
        String note = Messages.catalogue().get("report.manual_checklist_not_yet_evaluated");
        Map<List<String>, ObjectNode> gapSteps = gapStepIndex(blindSpots);
        List<ObjectNode> answers = new ArrayList<>();
        for (Assertions.Assertion a : assertions) {
            for (JsonNode xw : a.crosswalk) {
                String framework = Assess.fieldStr(xw, "framework");
                if (!BUYER_FRAMEWORKS.contains(framework)) {
                    continue;
                }
                ObjectNode entry = Json.nodes().objectNode();
                entry.put("framework", framework);
                entry.put("question", Assess.fieldStr(xw, "clause"));
                entry.put("control", a.control);
                entry.put("control_version", a.controlVersion);
                entry.put("subject", a.subject);
                entry.put("outcome", a.outcome);
                entry.put("mode", a.mode);
                JsonNode verified = xw.get("verified");
                entry.put("verified", verified != null && Readiness.pyTruthy(verified));
                ArrayNode evidence = entry.putArray("evidence");
                a.evidence.forEach(e -> evidence.add(e.toJson()));
                ObjectNode gap = "insufficient_evidence".equals(a.outcome)
                        ? gapSteps.get(refKey(a.subject, a.control, a.controlVersion))
                        : null;
                if (gap != null) {
                    entry.set("gap_step", gap);
                } else {
                    entry.putNull("gap_step");
                }
                if (MANUAL_MODES.contains(a.mode) && "not_assessed".equals(a.outcome)) {
                    entry.put("manual_checklist_note", note);
                }
                answers.add(entry);
            }
        }
        Comparator<ObjectNode> order = (x, y) -> 0;
        for (String field : SORT_FIELDS) {
            order = order.thenComparing(e -> e.get(field).asText(), Json::byteCompare);
        }
        answers.sort(order);

        ObjectNode view = Json.nodes().objectNode();
        ArrayNode answersOut = view.putArray("answers");
        answers.forEach(answersOut::add);
        ObjectNode byQuestion = view.putObject("by_question");
        BUYER_FRAMEWORKS.forEach(byQuestion::putObject);
        for (int index = 0; index < answers.size(); index++) {
            ObjectNode entry = answers.get(index);
            ObjectNode questions = (ObjectNode) byQuestion.get(entry.get("framework").asText());
            String question = entry.get("question").asText();
            ArrayNode list = questions.has(question) ? (ArrayNode) questions.get(question) : questions.putArray(question);
            list.add(index);
        }
        ObjectNode countsOut = view.putObject("counts");
        (counts != null ? counts : Assertions.aggregate(assertions)).forEach(countsOut::put);
        return view;
    }
}
