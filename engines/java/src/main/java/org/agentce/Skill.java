package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The agentce-fix-gaps skill folder (SPEC §7, §13.3; Appendix A2 (C)): {@code SKILL.md}, one
 * {@code findings/<control>--<n>.md} note per finding, and {@code REVERIFY.md}.
 *
 * <p>A faithful port of the Python reference's {@code render_skill_md}, {@code render_skill_finding_md},
 * {@code render_reverify_md}, {@code _tool_names_for_subject} and {@code _skill_finding_filename}
 * ({@code report.py}), as the TypeScript engine's {@code skill.ts} does: fixed text from the language-neutral
 * templates ({@code spec/report/templates/skill*.md.tmpl}, vendored under {@code resources/templates/}) and only
 * sanitised record- or catalog-derived strings, so every engine writes the same bytes.
 */
final class Skill {
    private Skill() {}

    /** PyYAML's {@code ESCAPE_REPLACEMENTS}: the short escapes of a double-quoted scalar. */
    private static final Map<Integer, String> ESCAPES = Map.ofEntries(
            Map.entry(0x00, "0"), Map.entry(0x07, "a"), Map.entry(0x08, "b"), Map.entry(0x09, "t"),
            Map.entry(0x0A, "n"), Map.entry(0x0B, "v"), Map.entry(0x0C, "f"), Map.entry(0x0D, "r"),
            Map.entry(0x1B, "e"), Map.entry(0x22, "\""), Map.entry(0x5C, "\\"), Map.entry(0x85, "N"),
            Map.entry(0xA0, "_"), Map.entry(0x2028, "L"), Map.entry(0x2029, "P"));

    /** Every distinct tool name this subject's own ToolCall events name, sanitised, in first-seen order
     * (Python's {@code _tool_names_for_subject}). */
    static List<String> toolNamesForSubject(List<JsonNode> subjectEvents) {
        Set<String> names = new LinkedHashSet<>();
        for (JsonNode event : subjectEvents) {
            JsonNode data = event.get("data");
            JsonNode type = data != null && data.isObject() ? data.get("@type") : null;
            if (type == null || !type.isTextual() || !"ToolCall".equals(type.asText())) {
                continue;
            }
            JsonNode tool = data.get("tool");
            JsonNode name = tool != null && tool.isObject() ? tool.get("name") : null;
            if (!Readiness.pyTruthy(name)) {
                continue;
            }
            names.add(Report.sanitizeForMarkdown(Readiness.pyStr(name)));
        }
        return new ArrayList<>(names);
    }

    /** One {@code findings/<control>--<n>.md} note from a remediation finding (Python's
     * {@code render_skill_finding_md}). */
    static String renderSkillFindingMd(JsonNode finding, List<String> toolCalls) {
        ObjectNode ctx = Remediation.sanitizeFinding(finding);
        ArrayNode calls = ctx.putArray("tool_calls");
        toolCalls.forEach(calls::add);
        return Templating.render(Bundled.template("skill-finding.md.tmpl"), ctx);
    }

    /** The generated skill's {@code SKILL.md} (Python's {@code render_skill_md}): the catalog-derived version lands
     * in YAML front matter, so it is sanitised and then written as a real double-quoted YAML scalar. */
    static String renderSkillMd(String specVersion, String cliVersion, String catalogVersion, String assertionsDigest) {
        ObjectNode ctx = Json.nodes().objectNode();
        ctx.put("spec_version", specVersion);
        ctx.put("cli_version", cliVersion);
        ctx.put("catalog_version", doubleQuoted(Report.sanitizeForMarkdown(catalogVersion)));
        ctx.put("assertions_digest", assertionsDigest);
        return Templating.render(Bundled.template("skill.md.tmpl"), ctx);
    }

    /** {@code REVERIFY.md} for the full re-verify command line, program name included (Python's
     * {@code render_reverify_md}). */
    static String renderReverifyMd(List<String> argv) {
        ObjectNode ctx = Json.nodes().objectNode();
        ArrayNode tokens = ctx.putArray("argv");
        argv.forEach(a -> tokens.add(Report.sanitizeForMarkdown(a)));
        return Templating.render(Bundled.template("skill-reverify.md.tmpl"), ctx);
    }

    /** A finding note's file name (Python's {@code _skill_finding_filename}). */
    static String skillFindingFilename(String control, int index) {
        return Report.safe(control) + "--" + index + ".md";
    }

    /** {@code yaml.safe_dump(text, default_style='"', allow_unicode=True, width=float("inf"))} without its trailing
     * newline: one double-quoted scalar, never folded, escaping what PyYAML's {@code write_double_quoted} would. */
    static String doubleQuoted(String text) {
        StringBuilder out = new StringBuilder("\"");
        text.codePoints().forEach(ch -> {
            boolean kept = ch != 0x22 && ch != 0x5C && ch != 0x85 && ch != 0x2028 && ch != 0x2029 && ch != 0xFEFF
                    && ((ch >= 0x20 && ch <= 0x7E) || (ch >= 0xA0 && ch <= 0xD7FF) || (ch >= 0xE000 && ch <= 0xFFFD));
            if (kept) {
                out.appendCodePoint(ch);
            } else if (ESCAPES.containsKey(ch)) {
                out.append('\\').append(ESCAPES.get(ch));
            } else if (ch <= 0xFF) {
                out.append(String.format(java.util.Locale.ROOT, "\\x%02X", ch));
            } else if (ch <= 0xFFFF) {
                out.append(String.format(java.util.Locale.ROOT, "\\u%04X", ch));
            } else {
                out.append(String.format(java.util.Locale.ROOT, "\\U%08X", ch));
            }
        });
        return out.append('"').toString();
    }
}
