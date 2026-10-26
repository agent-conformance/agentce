package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A minimal, logic-less Mustache-style renderer (SPEC §7: one language-neutral template in
 * {@code spec/}), a faithful port of the Python reference's {@code templating.py}: {@code {{path}}}
 * interpolation (dotted paths over a scope stack, innermost first), {@code {{#path}}} sections
 * (iterate a list, push a non-empty mapping, render once in the same scope for any other truthy
 * value), {@code {{^path}}} inverted sections and {@code {{.}}} for the current scope. Truthiness and
 * the text of a value are Python's, so every engine renders the same template to the same bytes.
 */
final class Templating {
    private static final Pattern TAG = Pattern.compile("\\{\\{([#^/]?)([a-zA-Z0-9_.]+|\\.)\\}\\}");

    private Templating() {}

    private record Node(String kind, String value, List<Node> children) {}

    private static List<Node> parse(String template) {
        List<Node> root = new ArrayList<>();
        List<List<Node>> stack = new ArrayList<>(List.of(root));
        List<String> openNames = new ArrayList<>();
        Matcher m = TAG.matcher(template);
        int pos = 0;
        while (m.find()) {
            List<Node> top = stack.get(stack.size() - 1);
            if (m.start() > pos) {
                top.add(new Node("text", template.substring(pos, m.start()), null));
            }
            String sigil = m.group(1);
            String name = m.group(2);
            if (sigil.equals("#") || sigil.equals("^")) {
                List<Node> children = new ArrayList<>();
                top.add(new Node(sigil.equals("#") ? "section" : "inverted", name, children));
                stack.add(children);
                openNames.add(name);
            } else if (sigil.equals("/")) {
                if (openNames.isEmpty() || !openNames.get(openNames.size() - 1).equals(name)) {
                    throw new IllegalArgumentException("mismatched closing tag {/" + name + "} in template");
                }
                openNames.remove(openNames.size() - 1);
                stack.remove(stack.size() - 1);
            } else {
                top.add(new Node("var", name, null));
            }
            pos = m.end();
        }
        if (pos < template.length()) {
            stack.get(stack.size() - 1).add(new Node("text", template.substring(pos), null));
        }
        if (!openNames.isEmpty()) {
            StringBuilder names = new StringBuilder("[");
            for (int i = 0; i < openNames.size(); i++) {
                names.append(i > 0 ? ", " : "").append('\'').append(openNames.get(i)).append('\'');
            }
            throw new IllegalArgumentException("unclosed section tag(s) in template: " + names + "]");
        }
        return root;
    }

    private static JsonNode lookup(List<JsonNode> scopes, String path) {
        if (path.equals(".")) {
            return scopes.get(scopes.size() - 1);
        }
        String[] parts = path.split("\\.", -1);
        for (int i = scopes.size() - 1; i >= 0; i--) {
            JsonNode scope = scopes.get(i);
            if (scope != null && scope.isObject() && scope.has(parts[0])) {
                JsonNode value = scope.get(parts[0]);
                for (int p = 1; p < parts.length; p++) {
                    value = value != null && value.isObject() ? value.get(parts[p]) : null;
                }
                return value;
            }
        }
        return null;
    }

    private static void render(List<Node> nodes, List<JsonNode> scopes, StringBuilder out) {
        for (Node node : nodes) {
            switch (node.kind()) {
                case "text" -> out.append(node.value());
                case "var" -> {
                    JsonNode value = lookup(scopes, node.value());
                    if (value != null && !value.isNull()) {
                        out.append(Readiness.pyStr(value));
                    }
                }
                case "section" -> {
                    JsonNode value = lookup(scopes, node.value());
                    if (value != null && value.isArray()) {
                        for (JsonNode item : value) {
                            render(node.children(), push(scopes, item), out);
                        }
                    } else if (value != null && value.isObject()) {
                        if (value.size() > 0) {
                            render(node.children(), push(scopes, value), out);
                        }
                    } else if (Readiness.pyTruthy(value)) {
                        render(node.children(), scopes, out);
                    }
                }
                default -> {
                    if (!Readiness.pyTruthy(lookup(scopes, node.value()))) {
                        render(node.children(), scopes, out);
                    }
                }
            }
        }
    }

    private static List<JsonNode> push(List<JsonNode> scopes, JsonNode scope) {
        List<JsonNode> next = new ArrayList<>(scopes);
        next.add(scope);
        return next;
    }

    /** Render {@code template} against {@code context}: a pure function of its two arguments. A
     * mismatched or unclosed section tag throws {@link IllegalArgumentException}. */
    static String render(String template, JsonNode context) {
        StringBuilder out = new StringBuilder();
        render(parse(template), List.of(context), out);
        return out.toString();
    }
}
