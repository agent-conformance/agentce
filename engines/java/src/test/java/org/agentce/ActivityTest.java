package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.Collections;
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

    /** A small object-tree builder for test fixtures: alternating {@code key, value} pairs, where a
     * {@code String} value becomes a text node, a {@code boolean} a boolean node, and an {@link
     * ObjectNode} is nested as-is -- so a fixture reads like the data it represents instead of a
     * hand-spliced JSON string. */
    private static ObjectNode obj(Object... kv) {
        ObjectNode node = Json.nodes().objectNode();
        for (int i = 0; i < kv.length; i += 2) {
            String key = (String) kv[i];
            Object value = kv[i + 1];
            if (value instanceof String s) {
                node.put(key, s);
            } else if (value instanceof Boolean b) {
                node.put(key, b);
            } else {
                node.set(key, (ObjectNode) value);
            }
        }
        return node;
    }

    private static JsonNode event(String eventType, ObjectNode extra, String sourceClass) {
        ObjectNode data = obj("@type", eventType, "agent", obj("id", SUBJECT));
        data.setAll(extra);
        return obj("id", "e-" + eventType, "subject", SUBJECT, "agentcesourceclass", sourceClass, "data", data);
    }

    private static JsonNode event(String eventType, ObjectNode extra) {
        return event(eventType, extra, "self_report");
    }

    private static Profile emptyProfile() {
        return new Profile();
    }

    private static Profile profileWith(List<String> declaredTools) {
        Profile profile = new Profile();
        Profile.Subject subject = new Profile.Subject();
        subject.id = SUBJECT;
        subject.role = "both";
        subject.declaredTools.addAll(declaredTools);
        profile.catalogs.add("eu-ai-act@2026.09");
        profile.subjects.add(subject);
        return profile;
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
                event("ModelCall", obj("model", obj("provider", "openai", "name", "gpt-x", "version_or_digest", "1"))),
                event(
                        "ToolCall",
                        obj("tool", obj("name", "search", "server", "mcp://s", "protocol", "mcp"), "effect_class", "read")),
                event(
                        "ToolCall",
                        obj(
                                "tool",
                                obj("name", "transfer_funds", "server", "mcp://s", "protocol", "mcp"),
                                "effect_class",
                                "irreversible")),
                event("ToolCall", obj("tool", obj("name", "search", "server", "mcp://s", "protocol", "mcp"))));
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
                event("ApprovalDecided", obj("outcome", "approve"), "self_report"),
                event("ApprovalDecided", obj("outcome", "approve"), "independent_system"),
                event("ApprovalDecided", obj("outcome", "reject"), "independent_system"));
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
                event("PolicyDecision", obj("decision", "deny")),
                event("PolicyDecision", obj("decision", "allow")),
                event("AuthzCheck", obj("allowed", false)),
                event("AuthzCheck", obj("allowed", true)),
                event("Refusal", obj("reason_class", "policy")));
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
                event("ToolCall", obj("tool", obj("name", "search", "server", "s", "protocol", "mcp"))),
                event("ModelCall", obj("model", obj("provider", "openai", "name", "gpt-x", "version_or_digest", "1"))));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        assertEquals(List.of("gpt-x"), texts(activity.get("undeclared").get("models")));
        assertEquals(List.of("search"), texts(activity.get("undeclared").get("tools")));
    }

    @Test
    void declaringAToolRemovesItFromUndeclared() {
        List<JsonNode> events = List.of(
                event("ToolCall", obj("tool", obj("name", "search", "server", "s", "protocol", "mcp"))),
                event("ToolCall", obj("tool", obj("name", "transfer_funds", "server", "s", "protocol", "mcp"))));
        ObjectNode activity = Activity.summarizeActivity(events, profileWith(List.of("search")));
        assertEquals(List.of("transfer_funds"), texts(activity.get("undeclared").get("tools")));
    }

    @Test
    void declaringEveryToolLeavesNothingUndeclared() {
        List<JsonNode> events =
                List.of(event("ToolCall", obj("tool", obj("name", "search", "server", "s", "protocol", "mcp"))));
        ObjectNode activity = Activity.summarizeActivity(events, profileWith(List.of("search")));
        assertTrue(texts(activity.get("undeclared").get("tools")).isEmpty());
    }

    @Test
    void nonDictDataAndUnrelatedEventTypesAreIgnored() {
        List<JsonNode> events = List.of(
                obj("id", "e1", "subject", SUBJECT, "data", "not-a-dict"), event("SessionStart", obj()));
        ObjectNode activity = Activity.summarizeActivity(events, emptyProfile());
        assertEquals(List.of(SUBJECT), texts(activity.get("agents")));
        assertEquals(0, activity.get("tools").size());
        assertEquals(0, activity.get("models").size());
    }

    @Test
    void summarizeActivityIsOrderIndependent() {
        List<JsonNode> events = List.of(
                event("ModelCall", obj("model", obj("provider", "openai", "name", "gpt-x", "version_or_digest", "1"))),
                event(
                        "ToolCall",
                        obj("tool", obj("name", "search", "server", "s", "protocol", "mcp"), "effect_class", "read")),
                event(
                        "ToolCall",
                        obj(
                                "tool",
                                obj("name", "transfer_funds", "server", "s", "protocol", "mcp"),
                                "effect_class",
                                "irreversible")),
                event("ApprovalDecided", obj("outcome", "reject"), "independent_system"),
                event("PolicyDecision", obj("decision", "deny")));
        Profile profile = profileWith(List.of("search"));
        ObjectNode forward = Activity.summarizeActivity(events, profile);
        List<JsonNode> reversed = new ArrayList<>(events);
        Collections.reverse(reversed);
        ObjectNode backward = Activity.summarizeActivity(reversed, profile);
        assertEquals(forward, backward);
    }
}
