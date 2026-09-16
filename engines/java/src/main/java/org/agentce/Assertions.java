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

    /** Build an assertion with the required base fields; optional fields are set on the returned object. */
    public static Assertion make(
            String control, String controlVersion, String subject, String outcome, int rung, String mode,
            String[] window, int[] population) {
        Assertion a = new Assertion();
        a.control = control;
        a.controlVersion = controlVersion;
        a.subject = subject;
        a.outcome = outcome;
        a.rung = rung;
        a.mode = mode;
        a.window = window;
        a.population = population;
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
