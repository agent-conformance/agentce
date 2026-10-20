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

    /** The resource budgets a task scope declares (SPEC §7.7.6 {@code budgets}), in the order CND-07 reads them. */
    public static final List<String> BUDGET_DIMENSIONS = List.of("tokens", "compute_seconds", "spend");

    /**
     * A declared budget.
     *
     * @param dimension one of {@link #BUDGET_DIMENSIONS}
     * @param limit a non-negative number, or null when unreadable (a limit no usage can be shown to stay within)
     */
    public record Budget(String dimension, Double limit) {}

    /**
     * One task type's declared effect scope (SPEC §7.7.6): the tools and the effect classes an action of
     * the task may use, as CND-01 reads them, and the resource budgets CND-07 reads.
     * {@code requiresApproval} marks classes for CND-02 and allows none by itself.
     *
     * @param allowedTools null when the scope lists no tools (any tool); otherwise the tools it allows
     * @param allowedEffectClasses the effect classes it allows; none when the scope lists none
     * @param requiresApproval the effect classes that need a human's approval (CND-02's marker)
     * @param budgets the declared budgets, in {@link #BUDGET_DIMENSIONS} order
     * @param authorisedPrincipalRoles the principal roles CND-04 accepts at the end of an instruction chain; null
     *     when the scope declares none or declares them unreadably
     */
    public record TaskScope(
            Set<String> allowedTools,
            Set<String> allowedEffectClasses,
            Set<String> requiresApproval,
            List<Budget> budgets,
            Set<String> authorisedPrincipalRoles) {

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
                                strings(entry.get("requires_approval")),
                                budgets(entry.get("budgets")),
                                roles(entry.get("authorised_principal_roles"))));
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
        JsonNode data = Readiness.toJsonNode(
                Readiness.loadPyyaml(path, "input.domain_binding_invalid", "the domain binding", true));
        if (data == null || !data.isObject()) {
            return empty();
        }
        return fromDict(data);
    }

    /**
     * The budgets a scope declares; a dimension left out declares no limit. A {@code budgets} value that
     * is not a mapping declares every dimension unreadable.
     */
    private static List<Budget> budgets(JsonNode value) {
        List<Budget> out = new ArrayList<>();
        if (value == null || value.isNull()) {
            return out;
        }
        if (!value.isObject()) {
            for (String dimension : BUDGET_DIMENSIONS) {
                out.add(new Budget(dimension, null));
            }
            return out;
        }
        for (String dimension : BUDGET_DIMENSIONS) {
            if (value.has(dimension)) {
                out.add(new Budget(dimension, limit(dimension, value.get(dimension))));
            }
        }
        return out;
    }

    /** The largest count every engine holds exactly (2^53 - 1); a token limit above it is unreadable, and a token
     * usage or sum above it saturates to {@link #TOKEN_CAP}, which exceeds every readable limit. */
    static final long MAX_COUNT = (1L << 53) - 1;
    static final long TOKEN_CAP = MAX_COUNT + 1;

    /** A declared budget limit: a non-negative number, a whole one for tokens; anything else is null. */
    private static Double limit(String dimension, JsonNode value) {
        if ("tokens".equals(dimension)) {
            Long tokens = wholeNumber(value);
            return tokens == null || tokens > MAX_COUNT ? null : tokens.doubleValue();
        }
        double limit = value.doubleValue();
        return value.isNumber() && Double.isFinite(limit) && limit >= 0 ? limit : null;
    }

    /** A non-negative whole number ({@code 5}, or {@code 5.0} as another engine's reader may give it), else null;
     * one past {@link #MAX_COUNT} reads {@link #TOKEN_CAP}, as a long cannot hold every such number. */
    static Long wholeNumber(JsonNode value) {
        if (value == null || !value.isNumber()) {
            return null;
        }
        double d = value.doubleValue();
        if (!(d >= 0) || Double.isInfinite(d) || d != Math.rint(d)) {
            return null;
        }
        return d > MAX_COUNT ? TOKEN_CAP : value.longValue();
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

    /** A scope's authorised_principal_roles, trimmed; null when absent or unreadable (not a list of strings), so a
     * bare string, null or a non-string element never reads as a shorter list (CND-04, inbox row 358). */
    private static Set<String> roles(JsonNode value) {
        if (value == null || !value.isArray()) {
            return null;
        }
        Set<String> out = new HashSet<>();
        for (JsonNode member : value) {
            if (!member.isTextual()) {
                return null;
            }
            out.add(Conformance.pyStrip(member.textValue()));
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
