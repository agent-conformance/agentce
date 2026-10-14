package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes. It
 * subclasses {@code agentce:Decision} and {@code agentce:ContextItem}, declares which decision types
 * are consequential, and records the oversight modality each requires; for the Conduct overlay it
 * declares each task type's effect scope (SPEC §7.7.6 {@code task_scopes}). A run with no binding is
 * valid.
 */
public final class DomainBinding {
    public static final String DECISION_ROOT = "agentce:Decision";
    public static final String CONTEXT_ROOT = "agentce:ContextItem";

    /** child class -> parent class (domain subclasses of Decision / ContextItem). */
    public final Map<String, String> subclasses = new HashMap<>();
    /** decision-type IRIs declared consequential. */
    public final Set<String> consequential = new HashSet<>();
    /** decision-type IRI -> the oversight modality it requires. */
    public final Map<String, String> requiredOversight = new HashMap<>();
    /** task type -> every scope declared for it, in order (an action must stay within all of them). */
    public final Map<String, List<TaskScope>> taskScopes = new LinkedHashMap<>();
    /** whether a task_scopes entry was unreadable (not a mapping, or no string task_type). */
    public boolean taskScopesMalformed;

    /**
     * One task type's declared effect scope (SPEC §7.7.6), as CND-01 reads it: the tools and the
     * effect classes an action of the task may use. {@code requiresApproval} marks classes for CND-02
     * and allows none by itself.
     *
     * @param allowedTools null when the scope lists no tools (any tool); otherwise the tools it allows
     * @param allowedEffectClasses the effect classes it allows; none when the scope lists none
     * @param requiresApproval the effect classes that need a human's approval (CND-02's marker)
     */
    public record TaskScope(
            Set<String> allowedTools, Set<String> allowedEffectClasses, Set<String> requiresApproval) {

        public boolean allows(String tool, String effectClass) {
            if (allowedTools != null && (tool == null || !allowedTools.contains(tool))) {
                return false;
            }
            return effectClass != null && allowedEffectClasses.contains(effectClass);
        }
    }

    public static DomainBinding empty() {
        return new DomainBinding();
    }

    public static DomainBinding fromDict(JsonNode data) {
        DomainBinding binding = new DomainBinding();
        JsonNode decisionTypes = data.get("decision_types");
        if (decisionTypes != null && decisionTypes.isArray()) {
            for (JsonNode entry : decisionTypes) {
                if (!entry.isObject() || !entry.has("id")) {
                    continue;
                }
                String id = entry.get("id").asText();
                binding.subclasses.put(
                        id, entry.has("subclass_of") ? entry.get("subclass_of").asText() : DECISION_ROOT);
                if (truthy(entry.get("consequential"))) {
                    binding.consequential.add(id);
                }
                JsonNode modality = entry.get("required_oversight_modality");
                if (modality != null && modality.isTextual()) {
                    binding.requiredOversight.put(id, modality.asText());
                }
            }
        }
        JsonNode contextClasses = data.get("context_classes");
        if (contextClasses != null && contextClasses.isArray()) {
            for (JsonNode entry : contextClasses) {
                if (entry.isObject() && entry.has("id")) {
                    binding.subclasses.put(
                            entry.get("id").asText(),
                            entry.has("subclass_of") ? entry.get("subclass_of").asText() : CONTEXT_ROOT);
                }
            }
        }
        JsonNode scopes = data.get("task_scopes");
        if (scopes != null && scopes.isArray()) {
            for (JsonNode entry : scopes) {
                JsonNode taskType = entry.get("task_type");
                if (!entry.isObject() || taskType == null || !taskType.isTextual()) {
                    binding.taskScopesMalformed = true;
                    continue;
                }
                JsonNode tools = entry.get("allowed_tools");
                binding.taskScopes
                        .computeIfAbsent(taskType.asText(), k -> new ArrayList<>())
                        .add(new TaskScope(
                                tools == null || tools.isNull() ? null : strings(tools),
                                strings(entry.get("allowed_effect_classes")),
                                strings(entry.get("requires_approval"))));
            }
        }
        return binding;
    }

    /**
     * The scopes a task's actions are held to: those declared for the task type the task names, else,
     * when the binding declares a single task type and no unreadable entry, that type's (every task is
     * of it), else none, so nothing the task does can be shown within a declared scope. An unreadable
     * entry may name another type, so it never lets the remaining one stand for all.
     */
    public List<TaskScope> scopesFor(String task) {
        List<TaskScope> named = taskScopes.get(task);
        if (named != null) {
            return named;
        }
        if (taskScopes.size() == 1 && !taskScopesMalformed) {
            return taskScopes.values().iterator().next();
        }
        return List.of();
    }

    public static DomainBinding load(Path path) {
        JsonNode data = Yaml.parseFile(path);
        if (data == null || !data.isObject()) {
            return empty();
        }
        return fromDict(data);
    }

    /** The string members of a declared list; anything else declares none. */
    private static Set<String> strings(JsonNode value) {
        Set<String> out = new HashSet<>();
        if (value != null && value.isArray()) {
            for (JsonNode member : value) {
                if (member.isTextual()) {
                    out.add(member.asText());
                }
            }
        }
        return out;
    }

    private static boolean truthy(JsonNode node) {
        if (node == null || node.isNull() || node.isMissingNode()) {
            return false;
        }
        if (node.isBoolean()) {
            return node.booleanValue();
        }
        if (node.isNumber()) {
            return node.asDouble() != 0.0;
        }
        if (node.isTextual()) {
            return !node.asText().isEmpty();
        }
        return true;
    }
}
