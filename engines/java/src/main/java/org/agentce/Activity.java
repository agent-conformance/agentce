package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * What your agents did (Hill 1): counted facts about a run, built only from the accepted events.
 *
 * <p>{@link #summarizeActivity} is the one place the agents, models, tools, actions by effect
 * class, approvals by who recorded them, actions denied or blocked, and tools/models the records
 * show but the profile never declared are computed; {@code report.md}, {@code report.html}, the
 * {@code --json} envelope, and the terminal all render that one node (the pattern {@link Verdict}
 * established), so they cannot disagree. Nothing here depends on event order, the clock, or the
 * locale. A faithful port of the Python reference ({@code agentce/activity.py}); field names, key
 * order, and sort order match exactly so {@code activity.json} is byte-identical across engines.
 */
public final class Activity {
    private Activity() {}

    /** Every {@code ToolCall.effect_class} this view counts, plus the bucket for a call that names none. */
    public static final List<String> EFFECT_CLASSES = List.of(
            "external_communication", "irreversible", "physical", "read", "spend", "unspecified", "write");
    /** The three evidence-source trust classes (SPEC §6.4) an {@code ApprovalDecided} event was recorded by. */
    public static final List<String> RECORDER_CLASSES =
            List.of("enforcement_point", "independent_system", "self_report");
    /** The four ways SPEC's authority events say no to an action. */
    public static final List<String> DENIED_KINDS =
            List.of("approval_rejected", "authz_denied", "policy_denied", "refused");

    private static String eventType(JsonNode data) {
        JsonNode type = data.get("@type");
        return type != null && type.isTextual() ? type.textValue() : "";
    }

    private static JsonNode eventData(JsonNode event) {
        JsonNode data = event.get("data");
        return data != null && data.isObject() ? data : null;
    }

    private static String textOr(JsonNode node, String fallback) {
        return node != null && node.isTextual() ? node.textValue() : fallback;
    }

    /** Compares tuples the way Python's {@code sorted()} compares tuples: elementwise, by byte order,
     * earlier elements taking priority. A joined-string key (e.g. on {@code "\u0000"}) cannot serve as
     * a map key here: two different tuples can join to the same string (a name containing the join
     * character collides with an adjacent field boundary), which would silently drop one tuple as a
     * duplicate and could disagree with the other engines' output. */
    private static int compareTuple(List<String> a, List<String> b) {
        int n = Math.min(a.size(), b.size());
        for (int i = 0; i < n; i++) {
            int c = Json.byteCompare(a.get(i), b.get(i));
            if (c != 0) return c;
        }
        return Integer.compare(a.size(), b.size());
    }

    /** Return the counted facts {@code events} show, compared against what {@code profile} declares.
     *
     * <p>{@code undeclared} names every distinct tool and model name the events show that no
     * subject's {@code declaredTools}/{@code declaredModels} names -- honestly "not declared yet",
     * never "suspicious": a profile that declares neither leaves every tool and model in that list,
     * which is the correct first-run answer, not a false positive. */
    public static ObjectNode summarizeActivity(List<JsonNode> events, Profile profile) {
        Set<String> agents = new TreeSet<>(Json::byteCompare);
        TreeMap<List<String>, String[]> models = new TreeMap<>(Activity::compareTuple);
        TreeMap<List<String>, String[]> tools = new TreeMap<>(Activity::compareTuple);
        Set<String> observedTools = new LinkedHashSet<>();
        Set<String> observedModels = new LinkedHashSet<>();
        var actionsByEffectClass = new java.util.LinkedHashMap<String, Integer>();
        for (String c : EFFECT_CLASSES) actionsByEffectClass.put(c, 0);
        var approvalsByRecorder = new java.util.LinkedHashMap<String, Integer>();
        for (String c : RECORDER_CLASSES) approvalsByRecorder.put(c, 0);
        var deniedOrBlocked = new java.util.LinkedHashMap<String, Integer>();
        for (String k : DENIED_KINDS) deniedOrBlocked.put(k, 0);

        for (JsonNode event : events) {
            JsonNode data = eventData(event);
            if (data == null) continue;
            JsonNode agent = data.get("agent");
            if (agent != null && agent.isObject() && agent.get("id") != null && agent.get("id").isTextual()) {
                agents.add(agent.get("id").textValue());
            }
            String etype = eventType(data);
            switch (etype) {
                case "ModelCall" -> {
                    JsonNode model = data.get("model");
                    if (model != null && model.isObject() && model.get("name") != null && model.get("name").isTextual()) {
                        String name = model.get("name").textValue();
                        observedModels.add(name);
                        String provider = textOr(model.get("provider"), "");
                        String versionOrDigest = textOr(model.get("version_or_digest"), "");
                        models.put(List.of(provider, name, versionOrDigest), new String[] {provider, name, versionOrDigest});
                    }
                }
                case "ToolCall" -> {
                    JsonNode tool = data.get("tool");
                    if (tool != null && tool.isObject() && tool.get("name") != null && tool.get("name").isTextual()) {
                        String name = tool.get("name").textValue();
                        observedTools.add(name);
                        String server = textOr(tool.get("server"), "");
                        String protocol = textOr(tool.get("protocol"), "");
                        tools.put(List.of(name, server, protocol), new String[] {name, server, protocol});
                    }
                    JsonNode effectClass = data.get("effect_class");
                    String key = effectClass != null && effectClass.isTextual()
                                    && actionsByEffectClass.containsKey(effectClass.textValue())
                            ? effectClass.textValue()
                            : "unspecified";
                    actionsByEffectClass.merge(key, 1, Integer::sum);
                }
                case "ApprovalDecided" -> {
                    String sourceClass = textOr(event.get("agentcesourceclass"), "");
                    if (approvalsByRecorder.containsKey(sourceClass)) {
                        approvalsByRecorder.merge(sourceClass, 1, Integer::sum);
                    }
                    JsonNode outcome = data.get("outcome");
                    if (outcome != null && "reject".equals(outcome.asText())) {
                        deniedOrBlocked.merge("approval_rejected", 1, Integer::sum);
                    }
                }
                case "PolicyDecision" -> {
                    JsonNode decision = data.get("decision");
                    if (decision != null && "deny".equals(decision.asText())) {
                        deniedOrBlocked.merge("policy_denied", 1, Integer::sum);
                    }
                }
                case "AuthzCheck" -> {
                    JsonNode allowed = data.get("allowed");
                    if (allowed != null && allowed.isBoolean() && !allowed.booleanValue()) {
                        deniedOrBlocked.merge("authz_denied", 1, Integer::sum);
                    }
                }
                case "Refusal" -> deniedOrBlocked.merge("refused", 1, Integer::sum);
                default -> {}
            }
        }

        Set<String> declaredTools = new LinkedHashSet<>();
        Set<String> declaredModels = new LinkedHashSet<>();
        for (Profile.Subject subject : profile.subjects) {
            declaredTools.addAll(subject.declaredTools);
            declaredModels.addAll(subject.declaredModels);
        }

        ObjectNode out = Json.nodes().objectNode();
        ArrayNode agentsArr = out.putArray("agents");
        agents.forEach(agentsArr::add);

        ArrayNode modelsArr = out.putArray("models");
        for (String[] m : models.values()) {
            ObjectNode node = modelsArr.addObject();
            node.put("provider", m[0]);
            node.put("name", m[1]);
            node.put("version_or_digest", m[2]);
        }

        ArrayNode toolsArr = out.putArray("tools");
        for (String[] t : tools.values()) {
            ObjectNode node = toolsArr.addObject();
            node.put("name", t[0]);
            node.put("server", t[1]);
            node.put("protocol", t[2]);
        }

        ObjectNode actionsNode = out.putObject("actions_by_effect_class");
        actionsByEffectClass.forEach(actionsNode::put);
        ObjectNode approvalsNode = out.putObject("approvals_by_recorder");
        approvalsByRecorder.forEach(approvalsNode::put);
        ObjectNode deniedNode = out.putObject("denied_or_blocked");
        deniedOrBlocked.forEach(deniedNode::put);

        ObjectNode undeclared = out.putObject("undeclared");
        TreeSet<String> undeclaredModels = new TreeSet<>(Json::byteCompare);
        for (String m : observedModels) if (!declaredModels.contains(m)) undeclaredModels.add(m);
        TreeSet<String> undeclaredTools = new TreeSet<>(Json::byteCompare);
        for (String t : observedTools) if (!declaredTools.contains(t)) undeclaredTools.add(t);
        ArrayNode undeclaredModelsArr = undeclared.putArray("models");
        undeclaredModels.forEach(undeclaredModelsArr::add);
        ArrayNode undeclaredToolsArr = undeclared.putArray("tools");
        undeclaredTools.forEach(undeclaredToolsArr::add);

        return out;
    }
}
