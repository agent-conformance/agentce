package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.Comparator;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * The security view (18.16): the same run, read for tool access, enforcement-point evidence, and
 * external-standard citations.
 *
 * <p>{@link #compute} adds no new rollup: {@code Activity#summarizeActivity} already counts tool
 * access, actions by effect class, approvals and denials, and drift, and every {@code
 * Assertions.Assertion} already carries its control's own {@code crosswalk} entries. This module only
 * selects and groups those two already-computed inputs -- it never re-scans events or re-derives a
 * verdict. A faithful port of the Python reference ({@code agentce/security_view.py}); field names, key
 * order, and sort order match exactly so {@code security.json} is byte-identical across engines.
 */
public final class SecurityView {
    private SecurityView() {}

    /**
     * The three external-standard frameworks the security view cites (contracts/P18-18.16.md); a
     * control's crosswalk entries against any other framework ({@code eu-ai-act}, {@code iso-42001},
     * {@code nist-ai-rmf}, {@code aiuc-1}) are out of scope for this view.
     */
    private static final Set<String> CITED_FRAMEWORKS = Set.of("owasp-asi-2026", "mitre-atlas", "owasp-acs");

    /**
     * Each framework's own {@code version:} field, copied by hand from its crosswalk file under {@code
     * spec/catalogs/base/eu-ai-act/crosswalk/}. Crosswalk files are not engine-loaded at assessment
     * time (the crosswalk README's own hygiene rule), so this is the one place a version bump is made
     * when a cited framework's file changes; consulted only by a render layer to show "mitre-atlas
     * 2026.09" rather than a bare framework id next to a clause. {@code security.json} itself carries
     * no version field.
     */
    public static final Map<String, String> FRAMEWORK_VERSIONS =
            Map.of("owasp-asi-2026", "2025.12", "mitre-atlas", "2026.09", "owasp-acs", "0.1.0");

    private record Citation(String control, String framework, String clause, boolean verified) {}

    private static final Comparator<Citation> ORDER =
            Comparator.comparing(Citation::control, Json::byteCompare)
                    .thenComparing(Citation::framework, Json::byteCompare)
                    .thenComparing(Citation::clause, Json::byteCompare)
                    .thenComparing(Citation::verified);

    /**
     * Return the security-framed selection of {@code activity}/{@code assertions} for a whole run.
     *
     * <p>Runs once over the pooled run regardless of subject count: unlike the project view's
     * per-agent split, "what could have stopped this run's actions" is a whole-run posture question.
     * Deterministic: no wall-clock, no locale, no assertion-order dependency ({@code
     * standards_citations} is deduplicated and sorted).
     */
    public static ObjectNode compute(ObjectNode activity, java.util.List<Assertions.Assertion> assertions) {
        TreeSet<Citation> citations = new TreeSet<>(ORDER);
        for (Assertions.Assertion a : assertions) {
            for (JsonNode e : a.crosswalk) {
                JsonNode frameworkNode = e.get("framework");
                String framework = frameworkNode != null ? frameworkNode.asText() : null;
                if (framework == null || !CITED_FRAMEWORKS.contains(framework)) {
                    continue;
                }
                JsonNode clauseNode = e.get("clause");
                JsonNode verifiedNode = e.get("verified");
                citations.add(new Citation(
                        a.control,
                        framework,
                        clauseNode != null ? clauseNode.asText() : "",
                        verifiedNode != null && verifiedNode.asBoolean()));
            }
        }

        ObjectNode out = Json.nodes().objectNode();
        out.set("tool_access", activity.get("tools"));
        out.set("actions_by_effect_class", activity.get("actions_by_effect_class"));
        ObjectNode enforcement = out.putObject("enforcement_point_evidence");
        enforcement.set("denied_or_blocked", activity.get("denied_or_blocked"));
        enforcement.set("approvals_by_recorder", activity.get("approvals_by_recorder"));
        ObjectNode undeclared = (ObjectNode) activity.get("undeclared");
        ObjectNode drift = out.putObject("drift");
        drift.set("tools", undeclared.get("tools"));
        drift.set("models", undeclared.get("models"));
        ArrayNode citationsOut = out.putArray("standards_citations");
        for (Citation c : citations) {
            ObjectNode node = citationsOut.addObject();
            node.put("control", c.control());
            node.put("framework", c.framework());
            node.put("clause", c.clause());
            node.put("verified", c.verified());
        }
        return out;
    }
}
