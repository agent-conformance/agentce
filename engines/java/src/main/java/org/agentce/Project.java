package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * The project view: every agent's records in one run, side by side (Hill 7).
 *
 * <p>A multi-subject run already assesses every subject; {@link #computeProjectView} rolls that up
 * into one ordered, per-agent view -- a summary row per subject (declared or not), the undeclared
 * agents nobody's profile named, and the top blind spots across the whole project, each naming which
 * agents it touches. Read-only over {@code Assess#assessSubjects}'/{@code Activity#summarizeActivity}'/
 * {@code BlindSpots#computeBlindSpots}'s own outputs: it never re-derives a verdict or re-scans events.
 * A faithful port of the Python reference ({@code agentce/project.py}); field names, key order, and
 * sort order match exactly so {@code project.json} is byte-identical across engines.
 */
public final class Project {
    private Project() {}

    /** How many blind spots {@link #computeProjectView} lists at the top level. */
    public static final int MAX_TOP_GAPS = 5;

    /**
     * Group {@code computeBlindSpots}'s global {@code blind_spots} list by the subjects each entry
     * actually names, RE-SCOPING each entry's {@code unlocked_checks}/{@code needed_by_checks}/
     * {@code checks_unlocked}/{@code needed_by} to that one subject rather than copying the global
     * counts (a global entry spanning two subjects must not report the other subject's checks or
     * count under either one).
     */
    public static Map<String, List<ObjectNode>> blindSpotsBySubject(ObjectNode blindSpots) {
        Map<String, List<ObjectNode>> bySubject = new LinkedHashMap<>();
        JsonNode entries = blindSpots.get("blind_spots");
        if (entries == null) {
            return bySubject;
        }
        for (JsonNode entryNode : entries) {
            ObjectNode entry = (ObjectNode) entryNode;
            JsonNode unlocked = entry.get("unlocked_checks");
            JsonNode needed = entry.get("needed_by_checks");
            TreeSet<String> subjects = new TreeSet<>(Json::byteCompare);
            unlocked.forEach(cr -> subjects.add(cr.get("subject").asText()));
            needed.forEach(cr -> subjects.add(cr.get("subject").asText()));
            for (String subject : subjects) {
                ArrayNode ownUnlocked = Json.nodes().arrayNode();
                unlocked.forEach(cr -> {
                    if (cr.get("subject").asText().equals(subject)) {
                        ownUnlocked.add(cr);
                    }
                });
                ArrayNode ownNeeded = Json.nodes().arrayNode();
                needed.forEach(cr -> {
                    if (cr.get("subject").asText().equals(subject)) {
                        ownNeeded.add(cr);
                    }
                });
                ObjectNode ownEntry = entry.deepCopy();
                ownEntry.set("unlocked_checks", ownUnlocked);
                ownEntry.set("needed_by_checks", ownNeeded);
                ownEntry.put("checks_unlocked", ownUnlocked.size());
                ownEntry.put("needed_by", ownNeeded.size());
                bySubject.computeIfAbsent(subject, k -> new ArrayList<>()).add(ownEntry);
            }
        }
        return bySubject;
    }

    /**
     * Group {@code computeBlindSpots}'s global {@code no_population} list by each entry's own
     * {@code subject} field: unlike {@code blind_spots}, every {@code no_population} entry already
     * names exactly one subject, so no re-scoping is needed, only grouping.
     */
    public static Map<String, List<ObjectNode>> noPopulationBySubject(ArrayNode noPopulation) {
        Map<String, List<ObjectNode>> bySubject = new LinkedHashMap<>();
        for (JsonNode n : noPopulation) {
            ObjectNode entry = (ObjectNode) n;
            bySubject.computeIfAbsent(entry.get("subject").asText(), k -> new ArrayList<>()).add(entry);
        }
        return bySubject;
    }

    private static ObjectNode summaryToJson(Verdict.Summary summary) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("verdict", summary.verdict());
        ObjectNode countsNode = node.putObject("counts");
        for (Map.Entry<String, Integer> e : summary.counts().entrySet()) {
            countsNode.put(e.getKey(), e.getValue());
        }
        ArrayNode topGapsArr = node.putArray("top_gaps");
        for (Verdict.Gap gap : summary.topGaps()) {
            ObjectNode gapNode = topGapsArr.addObject();
            gapNode.put("outcome", gap.outcome());
            ArrayNode controlsArr = gapNode.putArray("controls");
            gap.controls().forEach(controlsArr::add);
            gapNode.put("more", gap.more());
        }
        return node;
    }

    /**
     * Return {@code {"agents": [...], "undeclared_agents": [...], "top_gaps": [...]}} for a
     * multi-agent run.
     *
     * <p>{@code blindSpots} is {@code computeBlindSpots}'s own single, global return value (called
     * once, unchanged) -- this function derives every per-agent view from it, it never re-computes
     * blind spots per subject. Deterministic: no wall-clock, no locale, no filesystem-order
     * dependency.
     */
    public static ObjectNode computeProjectView(
            List<Assertions.Assertion> assertions,
            Profile profile,
            Set<String> declaredSubjectIds,
            Map<String, ObjectNode> activityBySubject,
            ObjectNode blindSpots) {
        return computeProjectView(assertions, profile, declaredSubjectIds, activityBySubject, blindSpots, null);
    }

    /** As the five-argument overload, with {@code deviations} (the already-linted register
     * {@code Assess#applyDeviations} would apply, SPEC §13.3.4) feeding each subject's own
     * {@code deviations} list -- control id plus the register's own {@code expiry} when present. */
    public static ObjectNode computeProjectView(
            List<Assertions.Assertion> assertions,
            Profile profile,
            Set<String> declaredSubjectIds,
            Map<String, ObjectNode> activityBySubject,
            ObjectNode blindSpots,
            List<ObjectNode> deviations) {
        Map<String, List<ObjectNode>> bySubject = blindSpotsBySubject(blindSpots);
        Map<String, ObjectNode> byControl = new LinkedHashMap<>();
        if (deviations != null) {
            for (ObjectNode d : deviations) {
                byControl.put(d.get("control").asText(), d);
            }
        }

        TreeSet<String> subjectIds = new TreeSet<>(Json::byteCompare);
        for (Assertions.Assertion a : assertions) {
            subjectIds.add(a.subject);
        }
        for (Profile.Subject s : profile.subjects) {
            subjectIds.add(s.id);
        }

        List<ObjectNode> agentRows = new ArrayList<>();
        Set<String> observedAgentsUnion = new LinkedHashSet<>();
        for (String subjectId : subjectIds) {
            List<Assertions.Assertion> subjectAssertions = new ArrayList<>();
            for (Assertions.Assertion a : assertions) {
                if (a.subject.equals(subjectId)) {
                    subjectAssertions.add(a);
                }
            }
            ObjectNode row = Json.nodes().objectNode();
            row.put("id", subjectId);
            row.put("declared", declaredSubjectIds.contains(subjectId));
            row.set("summary", summaryToJson(Verdict.summarize(subjectAssertions)));
            row.put("blind_spots_count", bySubject.getOrDefault(subjectId, List.of()).size());
            ArrayNode agentsObserved = Json.nodes().arrayNode();
            ObjectNode activity = activityBySubject.get(subjectId);
            JsonNode observed = activity != null ? activity.get("agents") : null;
            if (observed != null) {
                observed.forEach(agentsObserved::add);
                observed.forEach(n -> observedAgentsUnion.add(n.asText()));
            }
            row.set("agents_observed", agentsObserved);
            TreeSet<String> deviatedControls = new TreeSet<>(Json::byteCompare);
            for (Assertions.Assertion a : subjectAssertions) {
                if (a.deviation != null) {
                    deviatedControls.add(a.deviation);
                }
            }
            ArrayNode deviationsOut = Json.nodes().arrayNode();
            for (String control : deviatedControls) {
                ObjectNode entry = Json.nodes().objectNode();
                entry.put("control", control);
                ObjectNode registered = byControl.get(control);
                JsonNode expiry = registered != null ? registered.get("expiry") : null;
                if (expiry != null) {
                    entry.put("expiry", expiry.isTextual() ? expiry.asText() : expiry.toString());
                }
                deviationsOut.add(entry);
            }
            row.set("deviations", deviationsOut);
            agentRows.add(row);
        }

        TreeSet<String> undeclaredAgents = new TreeSet<>(Json::byteCompare);
        for (String agentId : observedAgentsUnion) {
            if (!declaredSubjectIds.contains(agentId)) {
                undeclaredAgents.add(agentId);
            }
        }

        ArrayNode topGaps = Json.nodes().arrayNode();
        JsonNode globalBlindSpots = blindSpots.get("blind_spots");
        if (globalBlindSpots != null) {
            int limit = Math.min(MAX_TOP_GAPS, globalBlindSpots.size());
            for (int i = 0; i < limit; i++) {
                ObjectNode entry = (ObjectNode) globalBlindSpots.get(i);
                ObjectNode copy = entry.deepCopy();
                TreeSet<String> agentsSet = new TreeSet<>(Json::byteCompare);
                entry.get("unlocked_checks").forEach(cr -> agentsSet.add(cr.get("subject").asText()));
                entry.get("needed_by_checks").forEach(cr -> agentsSet.add(cr.get("subject").asText()));
                ArrayNode agentsField = copy.putArray("agents");
                agentsSet.forEach(agentsField::add);
                topGaps.add(copy);
            }
        }

        ObjectNode out = Json.nodes().objectNode();
        ArrayNode agentsOut = out.putArray("agents");
        agentRows.forEach(agentsOut::add);
        ArrayNode undeclaredOut = out.putArray("undeclared_agents");
        undeclaredAgents.forEach(undeclaredOut::add);
        out.set("top_gaps", topGaps);
        return out;
    }
}
