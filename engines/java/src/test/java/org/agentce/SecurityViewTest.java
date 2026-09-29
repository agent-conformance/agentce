package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@link SecurityView#compute}: the security-framed selection of already-computed activity/crosswalk
 * facts (18.16).
 *
 * <p>A faithful port of the Python reference's {@code tests/test_security_view.py}'s {@code
 * compute_security_view} cases: same fixtures, same assertions, so all three engines are proven
 * against the same cases.
 */
class SecurityViewTest {

    private static final String[] WINDOW = {"2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"};

    private static ObjectNode emptyActivity() {
        ObjectNode activity = Json.nodes().objectNode();
        activity.putArray("tools");
        ObjectNode byEffectClass = activity.putObject("actions_by_effect_class");
        for (String key : new String[] {
            "external_communication", "irreversible", "physical", "read", "spend", "unspecified", "write"
        }) {
            byEffectClass.put(key, 0);
        }
        ObjectNode approvals = activity.putObject("approvals_by_recorder");
        for (String key : new String[] {"enforcement_point", "independent_system", "self_report"}) {
            approvals.put(key, 0);
        }
        ObjectNode denied = activity.putObject("denied_or_blocked");
        for (String key : new String[] {"approval_rejected", "authz_denied", "policy_denied", "refused"}) {
            denied.put(key, 0);
        }
        ObjectNode undeclared = activity.putObject("undeclared");
        undeclared.putArray("models");
        undeclared.putArray("tools");
        undeclared.putArray("agents");
        return activity;
    }

    private static ObjectNode crosswalkEntry(String framework, String clause, boolean verified) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("framework", framework);
        node.put("clause", clause);
        node.put("verified", verified);
        return node;
    }

    private static Assertions.Assertion assertion(String control, ObjectNode... crosswalk) {
        Assertions.Assertion a = Assertions.make(
                control, "2026.09", "spiffe://corp/agents/a", "conformant", 2, "automated", WINDOW,
                new int[] {1, 0}, "high", control.split("-", 1)[0]);
        for (ObjectNode entry : crosswalk) {
            a.crosswalk.add(entry);
        }
        return a;
    }

    @Test
    void computeFiltersToThreeFrameworksAndReusesActivity() {
        ObjectNode activity = emptyActivity();
        ArrayNode tools = (ArrayNode) activity.get("tools");
        ObjectNode tool = tools.addObject();
        tool.put("name", "shell");
        tool.put("server", "local");
        tool.put("protocol", "mcp");
        ((ObjectNode) activity.get("actions_by_effect_class")).put("irreversible", 2);
        ((ObjectNode) activity.get("actions_by_effect_class")).put("read", 5);

        List<Assertions.Assertion> assertions = List.of(
                assertion(
                        "ROB-02",
                        crosswalkEntry("mitre-atlas", "AML.T0051", false),
                        crosswalkEntry("eu-ai-act", "Art. 9", false)),
                assertion("REC-01", crosswalkEntry("owasp-acs", "Hook_SubagentStart", true)));

        ObjectNode view = SecurityView.compute(activity, assertions);

        assertEquals(activity.get("tools"), view.get("tool_access"));
        assertEquals(activity.get("actions_by_effect_class"), view.get("actions_by_effect_class"));
        assertEquals(
                activity.get("denied_or_blocked"),
                view.get("enforcement_point_evidence").get("denied_or_blocked"));
        assertEquals(
                activity.get("approvals_by_recorder"),
                view.get("enforcement_point_evidence").get("approvals_by_recorder"));
        assertEquals(0, view.get("drift").get("tools").size());
        assertEquals(0, view.get("drift").get("models").size());

        ArrayNode citations = (ArrayNode) view.get("standards_citations");
        assertEquals(2, citations.size());
        assertEquals("REC-01", citations.get(0).get("control").asText());
        assertEquals("owasp-acs", citations.get(0).get("framework").asText());
        assertEquals("Hook_SubagentStart", citations.get(0).get("clause").asText());
        assertEquals(true, citations.get(0).get("verified").asBoolean());
        assertEquals("ROB-02", citations.get(1).get("control").asText());
        assertEquals("mitre-atlas", citations.get(1).get("framework").asText());
        assertEquals("AML.T0051", citations.get(1).get("clause").asText());
        assertEquals(false, citations.get(1).get("verified").asBoolean());
    }

    @Test
    void computeDeduplicatesAndSortsRegardlessOfInputOrder() {
        ObjectNode activity = emptyActivity();
        ObjectNode dup = crosswalkEntry("mitre-atlas", "AML.T0101", false);
        List<Assertions.Assertion> assertionsA = List.of(assertion("INT-01", dup), assertion("OVS-03", dup));
        List<Assertions.Assertion> assertionsB = List.of(assertionsA.get(1), assertionsA.get(0));

        ObjectNode viewA = SecurityView.compute(activity, assertionsA);
        ObjectNode viewB = SecurityView.compute(activity, assertionsB);

        assertEquals(viewA.get("standards_citations"), viewB.get("standards_citations"));
        assertEquals(2, viewA.get("standards_citations").size());
    }
}
