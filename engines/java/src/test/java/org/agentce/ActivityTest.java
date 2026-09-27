package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@link Activity#summarizeActivity}: the counted facts a run's records show (18.4, Hill 1).
 *
 * <p>A faithful port of the Python reference's {@code tests/test_activity.py}: same event shapes,
 * same assertions, so all three engines are proven against the same cases.
 */
class ActivityTest {

    private static final String SUBJECT = "spiffe://corp/agents/a";

    private static JsonNode event(String eventType, String extraJson, String sourceClass) {
        String json = "{\"id\":\"e-" + eventType + "\",\"subject\":\"" + SUBJECT + "\","
                + "\"agentcesourceclass\":\"" + sourceClass + "\","
                + "\"data\":{\"@type\":\"" + eventType + "\",\"agent\":{\"id\":\"" + SUBJECT + "\"}" + extraJson + "}}";
        return Json.parse(json);
    }

    private static JsonNode event(String eventType, String extraJson) {
        return event(eventType, extraJson, "self_report");
    }

    private static Profile emptyProfile() {
        return new Profile();
    }

    private static Profile profileWith(List<String> declaredTools) {
        StringBuilder tools = new StringBuilder();
        for (int i = 0; i < declaredTools.size(); i++) {
            if (i > 0) tools.append(",");
            tools.append("\"").append(declaredTools.get(i)).append("\"");
        }
        String json = "{\"catalogs\":[\"eu-ai-act@2026.09\"],\"subjects\":[{\"id\":\"" + SUBJECT + "\","
                + "\"role\":\"both\",\"declared_tools\":[" + tools + "]}]}";
        return Profile.fromDict(Json.parse(json));
    }

    private static List<String> texts(JsonNode arr) {
        List<String> out = new ArrayList<>();
        arr.forEach(n -> out.add(n.asText()));
        return out;
    }

    @Test
    void emptyRunReportsAllZeroCounts() {
        ObjectNode activity = Activity.summarizeActivity(List.of(), emptyProfile());
        assertEquals(List.of(), texts(activity.get("agents")));
        assertEquals(0, activity.get("models").size());
        assertEquals(0, activity.get("tools").size());
        activity.get("actions_by_effect_class").forEach(n -> assertEquals(0, n.asInt()));
        activity.get("approvals_by_recorder").forEach(n -> assertEquals(0, n.asInt()));
        activity.get("denied_or_blocked").forEach(n -> assertEquals(0, n.asInt()));
        assertEquals(List.of(), texts(activity.get("undeclared").get("models")));
        assertEquals(List.of(), texts(activity.get("undeclared").get("tools")));
    }

    @Test
    void countsAgentsModelsAndTools() {
        List<JsonNode> events = List.of(
                event("ModelCall", ",\"model\":{\"provider\":\"openai\",\"name\":\"gpt-x\",\"version_or_digest\":\"1\"}"),
                event("ToolCall", ",\"tool\":{\"name\":\"search\",\"server\":\"mcp://s\",\"protocol\":\"mcp\"},\"effect_class\":\"read\""),
                event(
                        "ToolCall",
                        ",\"tool\":{\"name\":\"transfer_funds\",\"server\":\"mcp://s\",\"protocol\":\"mcp\"},\"effect_class\":\"irreversible\""),
                event("ToolCall", ",\"tool\":{\"name\":\"search\",\"server\":\"mcp://s\",\"protocol\":\"mcp\"}"));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        assertEquals(List.of(SUBJECT), texts(activity.get("agents")));
        assertEquals("gpt-x", activity.get("models").get(0).get("name").asText());
        List<String> toolNames = new ArrayList<>();
        activity.get("tools").forEach(n -> toolNames.add(n.get("name").asText()));
        toolNames.sort(String::compareTo);
        assertEquals(List.of("search", "transfer_funds"), toolNames);
        assertEquals(1, activity.get("actions_by_effect_class").get("read").asInt());
        assertEquals(1, activity.get("actions_by_effect_class").get("irreversible").asInt());
        assertEquals(1, activity.get("actions_by_effect_class").get("unspecified").asInt());
        assertEquals(0, activity.get("actions_by_effect_class").get("write").asInt());
    }

    @Test
    void approvalsCountedByWhoRecordedThem() {
        List<JsonNode> events = List.of(
                event("ApprovalDecided", ",\"outcome\":\"approve\"", "self_report"),
                event("ApprovalDecided", ",\"outcome\":\"approve\"", "independent_system"),
                event("ApprovalDecided", ",\"outcome\":\"reject\"", "independent_system"));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        ObjectNode approvals = (ObjectNode) activity.get("approvals_by_recorder");
        assertEquals(0, approvals.get("enforcement_point").asInt());
        assertEquals(2, approvals.get("independent_system").asInt());
        assertEquals(1, approvals.get("self_report").asInt());
        assertEquals(1, activity.get("denied_or_blocked").get("approval_rejected").asInt());
    }

    @Test
    void deniedOrBlockedCoversTheFourAuthoritySignals() {
        List<JsonNode> events = List.of(
                event("PolicyDecision", ",\"decision\":\"deny\""),
                event("PolicyDecision", ",\"decision\":\"allow\""),
                event("AuthzCheck", ",\"allowed\":false"),
                event("AuthzCheck", ",\"allowed\":true"),
                event("Refusal", ",\"reason_class\":\"policy\""));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        ObjectNode denied = (ObjectNode) activity.get("denied_or_blocked");
        assertEquals(0, denied.get("approval_rejected").asInt());
        assertEquals(1, denied.get("authz_denied").asInt());
        assertEquals(1, denied.get("policy_denied").asInt());
        assertEquals(1, denied.get("refused").asInt());
    }

    @Test
    void undeclaredToolAndModelAreHonestNotYetDeclared() {
        List<JsonNode> events = List.of(
                event("ToolCall", ",\"tool\":{\"name\":\"search\",\"server\":\"s\",\"protocol\":\"mcp\"}"),
                event("ModelCall", ",\"model\":{\"provider\":\"openai\",\"name\":\"gpt-x\",\"version_or_digest\":\"1\"}"));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        assertEquals(List.of("gpt-x"), texts(activity.get("undeclared").get("models")));
        assertEquals(List.of("search"), texts(activity.get("undeclared").get("tools")));
    }

    @Test
    void declaringAToolRemovesItFromUndeclared() {
        List<JsonNode> events = List.of(
                event("ToolCall", ",\"tool\":{\"name\":\"search\",\"server\":\"s\",\"protocol\":\"mcp\"}"),
                event("ToolCall", ",\"tool\":{\"name\":\"transfer_funds\",\"server\":\"s\",\"protocol\":\"mcp\"}"));
        ObjectNode activity = Activity.summarizeActivity(events, profileWith(List.of("search")));
        assertEquals(List.of("transfer_funds"), texts(activity.get("undeclared").get("tools")));
    }

    @Test
    void declaringEveryToolLeavesNothingUndeclared() {
        List<JsonNode> events =
                List.of(event("ToolCall", ",\"tool\":{\"name\":\"search\",\"server\":\"s\",\"protocol\":\"mcp\"}"));
        ObjectNode activity = Activity.summarizeActivity(events, profileWith(List.of("search")));
        assertTrue(texts(activity.get("undeclared").get("tools")).isEmpty());
    }

    @Test
    void nonDictDataAndUnrelatedEventTypesAreIgnored() {
        List<JsonNode> events = List.of(
                Json.parse("{\"id\":\"e1\",\"subject\":\"" + SUBJECT + "\",\"data\":\"not-a-dict\"}"),
                event("SessionStart", ""));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        assertEquals(List.of(SUBJECT), texts(activity.get("agents")));
        assertEquals(0, activity.get("tools").size());
        assertEquals(0, activity.get("models").size());
    }
}
