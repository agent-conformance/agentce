package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * The domain ontology binding (SPEC §6.5): the enterprise's decision and context classes. It
 * subclasses {@code agentce:Decision} and {@code agentce:ContextItem}, declares which decision types
 * are consequential, and records the oversight modality each requires. A run with no binding is valid.
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
        return binding;
    }

    public static DomainBinding load(Path path) {
        JsonNode data = Yaml.parseFile(path);
        if (data == null || !data.isObject()) {
            return empty();
        }
        return fromDict(data);
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
