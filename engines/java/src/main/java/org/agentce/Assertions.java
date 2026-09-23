package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Assertions: one machine-readable record per (control, subject) (SPEC §9.4, §9.2).
 *
 * <p>{@code assertions.json} is the source of truth from which report.md/html, OSCAL, and SARIF are
 * rendered; after RFC 8785 canonicalisation it is byte-identical across engines. Every supporting
 * verdict ({@code conformant}, {@code non-conformant}, {@code partial}) must cite an evidence pointer
 * (DC-5). A faithful port of the reference.
 */
public final class Assertions {
    private Assertions() {}

    public static final List<String> OUTCOMES = List.of(
            "conformant", "non-conformant", "partial", "not_applicable", "not_assessed", "insufficient_evidence");

    private static final Set<String> REQUIRE_EVIDENCE = Set.of("conformant", "non-conformant", "partial");

    /** An evidence pointer to a focus node (SPEC §9.4). */
    public static final class EvidencePointer {
        public final String ref;
        public final String digest;
        public final String sourceClass;

        public EvidencePointer(String ref, String digest, String sourceClass) {
            this.ref = ref;
            this.digest = digest;
            this.sourceClass = sourceClass;
        }

        public ObjectNode toJson() {
            ObjectNode out = Json.nodes().objectNode();
            out.put("ref", ref);
            out.put("digest", digest);
            out.put("source_class", sourceClass);
            return out;
        }
    }

    /** One (control, subject) verdict. Optional collections default empty; nullable fields default null. */
    public static final class Assertion {
        public String control;
        public String controlVersion;
        public String subject;
        public String outcome;
        public int rung;
        public String mode;
        public String[] window;
        public int[] population;
        public String severity = "";
        public String family = "";
        public List<JsonNode> expectations = new ArrayList<>();
        public List<JsonNode> violations = new ArrayList<>();
        public List<EvidencePointer> evidence = new ArrayList<>();
        public Boolean sourceClassSatisfied;
        public String evidenceStrength;
        public String deviation;
        public List<JsonNode> crosswalk = new ArrayList<>();

        public ObjectNode toJson() {
            ObjectNode record = Json.nodes().objectNode();
            record.put("control", control);
            record.put("control_version", controlVersion);
            record.put("subject", subject);
            record.put("outcome", outcome);
            record.put("rung", rung);
            record.put("mode", mode);
            ObjectNode w = record.putObject("window");
            w.put("start", window[0]);
            w.put("end", window[1]);
            ObjectNode pop = record.putObject("population");
            pop.put("applicable", population[0]);
            pop.put("failed", population[1]);
            record.put("severity", severity != null ? severity : "");
            record.put("family", family != null ? family : "");
            if (!expectations.isEmpty()) {
                ArrayNode a = record.putArray("expectations");
                expectations.forEach(a::add);
            }
            if (!violations.isEmpty()) {
                ArrayNode a = record.putArray("violations");
                violations.forEach(a::add);
            }
            if (!evidence.isEmpty()) {
                ArrayNode a = record.putArray("evidence");
                for (EvidencePointer e : evidence) {
                    a.add(e.toJson());
                }
            }
            if (sourceClassSatisfied != null) {
                record.put("source_class_satisfied", sourceClassSatisfied);
            }
            if (evidenceStrength != null) {
                record.put("evidence_strength", evidenceStrength);
            }
            if (deviation != null) {
                record.put("deviation", deviation);
            }
            if (!crosswalk.isEmpty()) {
                ArrayNode a = record.putArray("crosswalk");
                crosswalk.forEach(a::add);
            }
            return record;
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode v = node.get(field);
        return v != null ? v.asText() : "";
    }

    private static EvidencePointer evidencePointerFromJson(JsonNode data) {
        return new EvidencePointer(text(data, "ref"), text(data, "digest"), text(data, "source_class"));
    }

    /** Reconstruct an assertion from its JSON form (the inverse of {@link Assertion#toJson()}), for
     * re-rendering a report from a committed {@code assertions.json} (SPEC §9.4). */
    public static Assertion fromJson(JsonNode data) {
        Assertion a = new Assertion();
        a.control = text(data, "control");
        a.controlVersion = text(data, "control_version");
        a.subject = text(data, "subject");
        a.outcome = text(data, "outcome");
        JsonNode rung = data.get("rung");
        a.rung = rung != null && rung.isNumber() ? rung.asInt() : 0;
        a.mode = text(data, "mode");
        JsonNode window = data.get("window");
        a.window = new String[] {
            window != null ? text(window, "start") : "", window != null ? text(window, "end") : ""
        };
        JsonNode population = data.get("population");
        a.population = new int[] {
            population != null && population.get("applicable") != null ? population.get("applicable").asInt() : 0,
            population != null && population.get("failed") != null ? population.get("failed").asInt() : 0
        };
        a.severity = text(data, "severity");
        a.family = text(data, "family");
        JsonNode expectations = data.get("expectations");
        if (expectations != null && expectations.isArray()) {
            expectations.forEach(a.expectations::add);
        }
        JsonNode violations = data.get("violations");
        if (violations != null && violations.isArray()) {
            violations.forEach(a.violations::add);
        }
        JsonNode evidence = data.get("evidence");
        if (evidence != null && evidence.isArray()) {
            for (JsonNode e : evidence) {
                a.evidence.add(evidencePointerFromJson(e));
            }
        }
        JsonNode scs = data.get("source_class_satisfied");
        a.sourceClassSatisfied = scs != null && scs.isBoolean() ? scs.booleanValue() : null;
        JsonNode strength = data.get("evidence_strength");
        a.evidenceStrength = strength != null && strength.isTextual() ? strength.textValue() : null;
        JsonNode deviation = data.get("deviation");
        a.deviation = deviation != null && deviation.isTextual() ? deviation.textValue() : null;
        JsonNode crosswalk = data.get("crosswalk");
        if (crosswalk != null && crosswalk.isArray()) {
            crosswalk.forEach(a.crosswalk::add);
        }
        return a;
    }

    /** Build an assertion with the required base fields; optional fields are set on the returned object. */
    public static Assertion make(
            String control, String controlVersion, String subject, String outcome, int rung, String mode,
            String[] window, int[] population, String severity, String family) {
        Assertion a = new Assertion();
        a.control = control;
        a.controlVersion = controlVersion;
        a.subject = subject;
        a.outcome = outcome;
        a.rung = rung;
        a.mode = mode;
        a.window = window;
        a.population = population;
        a.severity = severity;
        a.family = family;
        return a;
    }

    /** Return the six-outcome counts (SPEC §9.2); every outcome is present, even at zero. */
    public static Map<String, Integer> aggregate(List<Assertion> assertions) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (String outcome : OUTCOMES) {
            counts.put(outcome, 0);
        }
        for (Assertion assertion : assertions) {
            counts.merge(assertion.outcome, 1, Integer::sum);
        }
        return counts;
    }

    /** Refuse to emit a supporting verdict with no evidence pointer (DC-5); raise on the first. */
    public static void checkDc5(List<Assertion> assertions) {
        for (Assertion assertion : assertions) {
            if (REQUIRE_EVIDENCE.contains(assertion.outcome) && assertion.evidence.isEmpty()) {
                throw new AgentceError(
                        "report.missing_evidence_pointer",
                        "control " + assertion.control + " on " + assertion.subject + " is " + assertion.outcome
                                + " but cites no evidence pointer.",
                        "every conformant, non-conformant, or partial outcome must cite evidence (DC-5).",
                        ExitCode.INPUT_ERROR.code);
            }
        }
    }
}
