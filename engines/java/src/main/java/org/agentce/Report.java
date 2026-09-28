package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Render the report artifacts from assertions (SPEC §9). {@code assertions.json} is written in RFC 8785
 * canonical form (byte-identical across engines); the human report (md/html), OSCAL Assessment Results,
 * SARIF, and role-aware evidence packs are rendered from it, and the reproducibility manifest records
 * the digest of every input and output. DC-5 is enforced before anything is written. A faithful port.
 */
public final class Report {
    private Report() {}

    private static final String ZERO_DIGEST = "sha256:" + "0".repeat(64);
    private static final byte[] NAMESPACE_URL = uuidToBytes(UUID.fromString("6ba7b811-9dad-11d1-80b4-00c04fd430c8"));

    private static final Map<String, String> SARIF_LEVEL = Map.of(
            "non-conformant", "error",
            "partial", "warning",
            "insufficient_evidence", "warning",
            "not_assessed", "note");
    private static final Map<String, String> OSCAL_STATE = Map.of(
            "conformant", "satisfied",
            "non-conformant", "not-satisfied",
            "partial", "not-satisfied",
            "not_applicable", "not-satisfied",
            "not_assessed", "not-satisfied",
            "insufficient_evidence", "not-satisfied");

    private static final String NON_DETERMINATION =
            "This statement reports conformance to the named catalog as evaluated by the Agent Conformance "
                    + "Engine over the named evidence and observation window. It is not a legal compliance "
                    + "determination.";

    private static byte[] uuidToBytes(UUID u) {
        ByteBuffer bb = ByteBuffer.allocate(16);
        bb.putLong(u.getMostSignificantBits());
        bb.putLong(u.getLeastSignificantBits());
        return bb.array();
    }

    /** RFC 4122 version-5 (SHA-1) UUID over the URL namespace and {@code name}, matching {@code uuid.uuid5}. */
    static String uuid5(String name) {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            sha1.update(NAMESPACE_URL);
            byte[] hash = sha1.digest(name.getBytes(StandardCharsets.UTF_8));
            byte[] b = java.util.Arrays.copyOf(hash, 16);
            b[6] = (byte) ((b[6] & 0x0F) | 0x50); // version 5
            b[8] = (byte) ((b[8] & 0x3F) | 0x80); // IETF variant
            long msb = 0;
            long lsb = 0;
            for (int i = 0; i < 8; i++) {
                msb = (msb << 8) | (b[i] & 0xFF);
            }
            for (int i = 8; i < 16; i++) {
                lsb = (lsb << 8) | (b[i] & 0xFF);
            }
            return new UUID(msb, lsb).toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-1 unavailable", e);
        }
    }

    private static String uuid(String... parts) {
        return uuid5("agentce:" + String.join(":", parts));
    }

    private static String digestBytes(byte[] data) {
        return "sha256:" + Canonical.sha256Hex(data);
    }

    private static String packageDigest() {
        return "sha256:" + Canonical.sha256Hex((Version.ENGINE_NAME + ":" + Version.ENGINE_VERSION).getBytes(StandardCharsets.UTF_8));
    }

    private static String safe(String name) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            out.append(Character.isLetterOrDigit(c) || c == '-' || c == '.' || c == '_' ? c : '_');
        }
        return out.toString();
    }

    /** Every control, keyed by id, across every resolved catalog -- so a finding can carry its
     * control's title (SPEC §9.3). A later catalog in the list (an overlay) wins over an earlier one
     * (the base) for the same control id. */
    private static Map<String, Catalog.ControlSpec> controlIndex(List<Catalog> catalogs) {
        Map<String, Catalog.ControlSpec> index = new LinkedHashMap<>();
        for (Catalog catalog : catalogs) {
            for (Catalog.ControlSpec control : catalog.controls) {
                index.put(control.id, control);
            }
        }
        return index;
    }

    /** A stable, deterministic reference URL for the control's family page (docs/reference/catalog/,
     * published at the site under the same path). SARIF's {@code helpUri} is a citation, not a runtime
     * dependency: it does not need to resolve for the check that reads it, the same way a JSON Schema
     * {@code $id} does not (spec/report/vendor/README.md). */
    private static String sarifHelpUri(String control) {
        String family = control.split("-", 2)[0];
        return "https://agent-conformance.org/reference/catalog/" + family;
    }

    private static String sarifRuleHelpText(String control, Catalog.ControlSpec spec) {
        return spec != null ? spec.title : "AgentCE control " + control + ".";
    }

    /** A fingerprint derived only from the assertion's own content -- control, subject, outcome, and
     * the evaluation window/population that produced it -- so two independent offline runs over the
     * same evidence produce byte-identical fingerprints (no clock, host, or run counter). */
    private static String sarifFingerprint(Assertions.Assertion a) {
        String payload = String.join(
                "|",
                a.control,
                a.subject,
                a.outcome,
                a.window[0],
                a.window[1],
                String.valueOf(a.population[0]),
                String.valueOf(a.population[1]));
        return Canonical.sha256Hex(payload.getBytes(StandardCharsets.UTF_8));
    }

    private static String outcomeLabel(Map<String, String> cat, String outcome) {
        return cat.getOrDefault("outcome." + outcome, outcome);
    }

    /** One clause citation, labelled unverified when the carried flag is not true (SPEC §7.3). */
    private static String crosswalkText(JsonNode entry, Map<String, String> cat) {
        JsonNode framework = entry.get("framework");
        JsonNode clause = entry.get("clause");
        String text = (framework != null ? framework.asText("") : "") + " "
                + (clause != null ? clause.asText("") : "");
        text = text.strip();
        JsonNode verified = entry.get("verified");
        if (verified == null || !verified.isBoolean() || !verified.booleanValue()) {
            text += " " + cat.get("report.crosswalk_unverified");
        }
        return text;
    }

    private static String esc(String s) {
        return s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&#x27;");
    }

    /** Cap applied when a record-derived string is sanitised for Markdown/terminal/HTML rendering
     * (SPEC §7 injection hardening): long enough to stay useful, short enough to bound a hostile
     * payload. */
    private static final int SANITIZE_CAP = 200;

    /** Shown in place of a name that neutralises to nothing: escaping, never erasure -- real
     * activity is never silently dropped to "0". */
    private static final String SANITIZE_EMPTY_NAME_PLACEHOLDER = "(unnamed)";

    /** Shown in place of a subject id, evidence ref, or violation path that neutralises to nothing --
     * {@link #SANITIZE_EMPTY_NAME_PLACEHOLDER}'s "(unnamed)" reads oddly for a field that was never a
     * name. */
    private static final String SANITIZE_EMPTY_FIELD_PLACEHOLDER = "(empty)";

    /** The complete, current Unicode {@code Default_Ignorable_Code_Point} property, as (first, last)
     * inclusive codepoint ranges -- hard-coded once and identical across all three engines,
     * independent of any engine's own Unicode database version ({@code Cf} alone misses variation
     * selectors, CGJ, the Mongolian free variation selectors, the Hangul fillers, and every
     * reserved-for-future-use DICP range). Mirrors {@code engines/python/agentce/report.py}'s
     * {@code _DICP_RANGES} exactly. */
    private static final int[][] DICP_RANGES = {
        {0x00AD, 0x00AD},
        {0x034F, 0x034F},
        {0x061C, 0x061C},
        {0x115F, 0x1160},
        {0x17B4, 0x17B5},
        {0x180B, 0x180F},
        {0x200B, 0x200F},
        {0x202A, 0x202E},
        {0x2060, 0x206F},
        {0x3164, 0x3164},
        {0xFE00, 0xFE0F},
        {0xFEFF, 0xFEFF},
        {0xFFA0, 0xFFA0},
        {0xFFF0, 0xFFF8},
        {0x1BCA0, 0x1BCA3},
        {0x1D173, 0x1D17A},
        {0xE0000, 0xE0FFF},
    };

    static boolean isDicpCodepoint(int codepoint) {
        for (int[] range : DICP_RANGES) {
            if (codepoint >= range[0] && codepoint <= range[1]) {
                return true;
            }
        }
        return false;
    }

    /** Whether any codepoint in {@code text} is in the drop-set {@link #neutralize} uses (category
     * {@code Cf} or the hard-coded {@code Default_Ignorable_Code_Point} table above). */
    public static boolean hasInvisibleCodepoint(String text) {
        return text.codePoints().anyMatch(cp -> Character.getType(cp) == Character.FORMAT || isDicpCodepoint(cp));
    }

    /** The one shared core every sanitiser target calls first (SPEC §7 injection hardening,
     * mirroring the Python reference's {@code _neutralize}): replace every {@code Cc}/{@code Co}/
     * {@code Cs}/{@code Zl}/{@code Zp} codepoint or {@code Zs} (NBSP, ideographic space, ...) with a
     * literal space, folding each run into one (one explicit, per-engine-identical definition of
     * "collapsible whitespace", category-based, not Java's own differing built-in
     * {@code isWhitespace}, which is exactly what let NBSP/U+3000 diverge across engines before);
     * drop every {@code Cf}-or-{@code Default_Ignorable_Code_Point} codepoint entirely (never a space
     * -- removing a zero-width character preserves the string's visual intent); trim leading and
     * trailing whitespace; cap by codepoint (never a UTF-16 half of a surrogate pair), computed here
     * on the neutralized-but-not-yet-HTML-escaped text, before any HTML-entity expansion a caller
     * applies on top; then render {@code placeholder} if the result is empty but {@code text} was
     * not. */
    private static String neutralize(String text, int cap, String placeholder) {
        StringBuilder kept = new StringBuilder();
        boolean inWs = false;
        int i = 0;
        while (i < text.length()) {
            int cp = text.codePointAt(i);
            i += Character.charCount(cp);
            int type = Character.getType(cp);
            if (cp == ' '
                    || type == Character.CONTROL
                    || type == Character.PRIVATE_USE
                    || type == Character.SURROGATE
                    || type == Character.LINE_SEPARATOR
                    || type == Character.PARAGRAPH_SEPARATOR
                    || type == Character.SPACE_SEPARATOR) {
                if (!inWs) {
                    kept.append(' ');
                    inWs = true;
                }
            } else if (type == Character.FORMAT || isDicpCodepoint(cp)) {
                // Dropped entirely (never a space): by definition invisible/zero-width.
            } else {
                kept.appendCodePoint(cp);
                inWs = false;
            }
        }
        String collapsed = kept.toString().strip();
        int codepointCount = collapsed.codePointCount(0, collapsed.length());
        if (codepointCount > cap) {
            int[] codepoints = collapsed.codePoints().toArray();
            collapsed = new String(codepoints, 0, cap - 1) + "…";
        }
        if (collapsed.isEmpty() && !text.isEmpty()) {
            collapsed = placeholder;
        }
        return collapsed;
    }

    /** The six substitutions {@link #sanitizeForMarkdown} applies on top of {@link #neutralize}'s
     * output -- see {@code engines/python/agentce/report.py}'s {@code _MARKDOWN_SUBSTITUTIONS} for
     * the full rationale (breaks code-span/HTML/link/image/entity-reference syntax; deliberately not
     * a broader "fullwidth every punctuation character" rule). */
    private static final String[][] MARKDOWN_SUBSTITUTIONS = {
        {"`", "'"},
        {"<", "‹"},
        {">", "›"},
        {"[", "［"},
        {"]", "］"},
        {"&", "＆"},
    };

    /** Neutralise a record-derived string before it reaches {@code report.md}'s Markdown rendering or
     * the terminal (SPEC §7 injection hardening; mirrors the Python reference's
     * {@code sanitize_for_markdown}). */
    public static String sanitizeForMarkdown(String text, String placeholder, int cap) {
        String neutralized = neutralize(text, cap, placeholder);
        for (String[] sub : MARKDOWN_SUBSTITUTIONS) {
            neutralized = neutralized.replace(sub[0], sub[1]);
        }
        return neutralized;
    }

    public static String sanitizeForMarkdown(String text, String placeholder) {
        return sanitizeForMarkdown(text, placeholder, SANITIZE_CAP);
    }

    public static String sanitizeForMarkdown(String text) {
        return sanitizeForMarkdown(text, SANITIZE_EMPTY_NAME_PLACEHOLDER, SANITIZE_CAP);
    }

    /** A documented alias for {@link #sanitizeForMarkdown}, not a second implementation -- see the
     * Python reference's own {@code sanitize_for_terminal} docstring for why. Only the no-placeholder
     * form is ever called (every terminal call site uses the default name placeholder); the
     * placeholder/cap parameters live on {@link #sanitizeForMarkdown} directly. */
    public static String sanitizeForTerminal(String text) {
        return sanitizeForMarkdown(text);
    }

    /** Neutralise a record-derived string for HTML rendering: {@link #neutralize} first, then
     * {@link #esc} on the result -- nothing else (mirrors the Python reference's
     * {@code sanitize_for_html}). */
    public static String sanitizeForHtml(String text, String placeholder, int cap) {
        return esc(neutralize(text, cap, placeholder));
    }

    public static String sanitizeForHtml(String text, String placeholder) {
        return sanitizeForHtml(text, placeholder, SANITIZE_CAP);
    }

    public static String sanitizeForHtml(String text) {
        return sanitizeForHtml(text, SANITIZE_EMPTY_NAME_PLACEHOLDER, SANITIZE_CAP);
    }

    /** {@link #sanitizeForMarkdown} with the field placeholder rather than the name placeholder
     * (mirrors the Python reference's {@code _sanitize_field}). */
    private static String sanitizeField(String text) {
        return sanitizeForMarkdown(text, SANITIZE_EMPTY_FIELD_PLACEHOLDER);
    }

    /** {@code "label 3, label 2"} for every nonzero count, in the node's (fixed) field order; {@code
     * "0"} when every count is zero -- shared by the Markdown, HTML, and terminal renderings. {@code
     * labelOf} translates a key to its catalogue label; a key with no translation (the effect
     * classes, which are stable identifiers, not prose) stands for itself. */
    private static String activityTallyText(ObjectNode counts, Map<String, String> labelOf) {
        List<String> parts = new ArrayList<>();
        var it = counts.fields();
        while (it.hasNext()) {
            Map.Entry<String, JsonNode> e = it.next();
            int n = e.getValue().asInt();
            if (n != 0) {
                String label = labelOf != null ? labelOf.getOrDefault(e.getKey(), e.getKey()) : e.getKey();
                parts.add(label + " " + n);
            }
        }
        return parts.isEmpty() ? "0" : String.join(", ", parts);
    }

    private static List<String> activityUndeclaredLines(JsonNode undeclared, Map<String, String> cat) {
        List<String> tools = new ArrayList<>();
        undeclared.get("tools").forEach(n -> tools.add(n.asText()));
        List<String> models = new ArrayList<>();
        undeclared.get("models").forEach(n -> models.add(n.asText()));
        if (tools.isEmpty() && models.isEmpty()) {
            return List.of(cat.get("report.activity_none_undeclared"));
        }
        List<String> lines = new ArrayList<>();
        if (!tools.isEmpty()) {
            lines.add(cat.get("report.activity_undeclared_tools_label") + ": "
                    + tools.stream().map(Report::sanitizeForMarkdown).collect(Collectors.joining(", ")));
        }
        if (!models.isEmpty()) {
            lines.add(cat.get("report.activity_undeclared_models_label") + ": "
                    + models.stream().map(Report::sanitizeForMarkdown).collect(Collectors.joining(", ")));
        }
        return lines;
    }

    /** {@code (label, value)} for every counted-facts row -- the one place the row set and order is
     * decided, shared by the Markdown, HTML, and terminal renderings. Agent, model, and tool names
     * are event-derived strings (SPEC §7 injection hardening), escaped with {@link #sanitizeForMarkdown} before
     * joining so a hostile name (embedded newlines) can never start a new Markdown/terminal line --
     * this section renders before the verdict. */
    private static List<Map.Entry<String, String>> activityRows(ObjectNode activity, Map<String, String> cat) {
        Map<String, String> recorderLabels = new LinkedHashMap<>();
        for (String k : Activity.RECORDER_CLASSES) recorderLabels.put(k, cat.get("report.activity_recorder_" + k));
        Map<String, String> deniedLabels = new LinkedHashMap<>();
        for (String k : Activity.DENIED_KINDS) deniedLabels.put(k, cat.get("report.activity_denied_" + k));

        List<String> agentNames = new ArrayList<>();
        activity.get("agents").forEach(n -> agentNames.add(sanitizeForMarkdown(n.asText())));
        List<String> modelNames = new ArrayList<>();
        activity.get("models").forEach(n -> modelNames.add(sanitizeForMarkdown(n.get("name").asText())));
        List<String> toolNames = new ArrayList<>();
        activity.get("tools").forEach(n -> toolNames.add(sanitizeForMarkdown(n.get("name").asText())));

        List<Map.Entry<String, String>> rows = new ArrayList<>();
        rows.add(Map.entry(
                cat.get("report.activity_agents_label"),
                agentNames.isEmpty() ? cat.get("report.activity_none_agents") : String.join(", ", agentNames)));
        rows.add(Map.entry(
                cat.get("report.activity_models_label"), modelNames.isEmpty() ? "0" : String.join(", ", modelNames)));
        rows.add(Map.entry(
                cat.get("report.activity_tools_label"), toolNames.isEmpty() ? "0" : String.join(", ", toolNames)));
        rows.add(Map.entry(
                cat.get("report.activity_actions_label"),
                activityTallyText((ObjectNode) activity.get("actions_by_effect_class"), null)));
        rows.add(Map.entry(
                cat.get("report.activity_approvals_label"),
                activityTallyText((ObjectNode) activity.get("approvals_by_recorder"), recorderLabels)));
        rows.add(Map.entry(
                cat.get("report.activity_denied_label"),
                activityTallyText((ObjectNode) activity.get("denied_or_blocked"), deniedLabels)));
        return rows;
    }

    /** The lines that lead the report body (before the verdict, SPEC's evidence-first framing): what
     * the records show your agents did, regardless of how they measure up. */
    private static List<String> activityMd(ObjectNode activity, Map<String, String> cat) {
        List<String> lines = new ArrayList<>();
        lines.add("## " + cat.get("report.activity_heading"));
        lines.add("");
        for (Map.Entry<String, String> row : activityRows(activity, cat)) {
            lines.add("- " + row.getKey() + ": " + row.getValue());
        }
        lines.add("");
        lines.add("### " + cat.get("report.activity_undeclared_heading"));
        lines.add("");
        for (String line : activityUndeclaredLines(activity.get("undeclared"), cat)) {
            lines.add("- " + line);
        }
        lines.add("");
        return lines;
    }

    private static String activityHtml(ObjectNode activity, Map<String, String> cat) {
        StringBuilder items = new StringBuilder();
        for (Map.Entry<String, String> row : activityRows(activity, cat)) {
            items.append("<li>").append(esc(row.getKey())).append(": ").append(esc(row.getValue())).append("</li>");
        }
        StringBuilder undeclared = new StringBuilder();
        for (String line : activityUndeclaredLines(activity.get("undeclared"), cat)) {
            undeclared.append("<p>").append(esc(line)).append("</p>");
        }
        return "<section aria-labelledby=\"activity\"><h2 id=\"activity\">" + esc(cat.get("report.activity_heading"))
                + "</h2><ul>" + items + "</ul>"
                + "<h3>" + esc(cat.get("report.activity_undeclared_heading")) + "</h3>" + undeclared
                + "</section>";
    }

    /** The lines a command prints for {@code activity}: agents, tools, models, and anything not yet
     * declared -- the same node {@link #activityMd}/{@link #activityHtml} render. */
    public static List<String> activityCliLines(ObjectNode activity, Map<String, String> cat) {
        List<String> lines = new ArrayList<>();
        for (Map.Entry<String, String> row : activityRows(activity, cat)) {
            lines.add(row.getKey() + ": " + row.getValue());
        }
        lines.add(cat.get("report.activity_undeclared_heading") + ":");
        for (String line : activityUndeclaredLines(activity.get("undeclared"), cat)) {
            lines.add("  " + line);
        }
        return lines;
    }

    private static final Map<String, String> BLIND_SPOT_OWNER_LABEL = Map.of(
            "agent_team", "the agent team",
            "platform_or_security", "platform or security",
            "ticketing_or_iam", "whoever runs ticketing or IAM");

    /** No English string is stored in {@code blind-spots.json} itself (RFC 0008 Sec.7): the artifact
     * carries only {@code owner_key}/{@code step_kind} tokens, and only the rendered report resolves
     * them to text. Unlike the Python engine, this text is never routed through the message
     * catalogue (RFC 0008 Sec.7: "the pre-existing, accepted scope boundary that rendered
     * report.md/report.html output has never been a three-engine byte-identity requirement"). */
    private static String blindSpotStepText(String stepKind, String ownerLabel) {
        return "request".equals(stepKind) ? "a request to " + ownerLabel : "a code change for " + ownerLabel;
    }

    /** {@code (label, value)} for every blind spot, in the module's own ranked order (never re-sorted
     * here). */
    private static List<Map.Entry<String, String>> blindSpotRows(ArrayNode blindSpots) {
        List<Map.Entry<String, String>> rows = new ArrayList<>();
        for (JsonNode bs : blindSpots) {
            String ownerLabel = BLIND_SPOT_OWNER_LABEL.get(bs.get("owner_key").asText());
            String step = blindSpotStepText(bs.get("step_kind").asText(), ownerLabel);
            List<String> adapterNames = new ArrayList<>();
            bs.get("supplying_adapters").forEach(n -> adapterNames.add(sanitizeField(n.asText())));
            String adapters = adapterNames.isEmpty() ? "no adapter today" : String.join(", ", adapterNames);
            String value = "unlocks " + bs.get("checks_unlocked").asInt() + " check(s), needed by "
                    + bs.get("needed_by").asInt() + " more; rung " + bs.get("ladder_rung").asInt() + " -- " + step
                    + ". Adapters that can supply this: " + adapters + ".";
            String label = sanitizeField(bs.get("event").asText()) + " (" + sanitizeField(bs.get("class").asText()) + ")";
            rows.add(Map.entry(label, value));
        }
        return rows;
    }

    private static List<Map.Entry<String, String>> noPopulationRows(ArrayNode noPopulation) {
        List<Map.Entry<String, String>> rows = new ArrayList<>();
        for (JsonNode entry : noPopulation) {
            String control = sanitizeField(entry.get("control").asText());
            String label = control + " on " + sanitizeField(entry.get("subject").asText()) + " ("
                    + sanitizeField(entry.get("catalog").asText()) + "@"
                    + sanitizeField(entry.get("control_version").asText())
                    + ")";
            String value = "The records show every kind of evidence " + control + " asks for, but not enough of "
                    + "it in the shape the control expects -- a --domain binding may be needed to identify the "
                    + "relevant decisions; see the control's documentation for what it needs.";
            rows.add(Map.entry(label, value));
        }
        return rows;
    }

    private static List<String> blindSpotsMd(ObjectNode blindSpots) {
        List<Map.Entry<String, String>> rows = blindSpotRows((ArrayNode) blindSpots.get("blind_spots"));
        List<Map.Entry<String, String>> noPopRows = noPopulationRows((ArrayNode) blindSpots.get("no_population"));
        List<String> lines = new ArrayList<>();
        lines.add("## Where your records can't show it yet");
        lines.add("");
        if (rows.isEmpty() && noPopRows.isEmpty()) {
            lines.add("- every check either has enough evidence, or nothing here would unlock more");
            lines.add("");
            return lines;
        }
        // The label is backtick-wrapped, not just interpolated after the list marker: a sanitised
        // value alone can still start with `#`/`~~~`/a digit-`.` sequence CommonMark parses as a
        // heading, code fence, or nested list when it is the first token of a list item's content. A
        // single leading backtick means the payload's own leading character is never the line's first
        // content, and is always the only backtick on the line since sanitizeForMarkdown already
        // substitutes any embedded backtick.
        for (Map.Entry<String, String> row : rows) {
            lines.add("- `" + row.getKey() + "`: " + row.getValue());
        }
        if (!noPopRows.isEmpty()) {
            lines.add("");
            lines.add("### Records that don't show enough, with no single fix");
            lines.add("");
            for (Map.Entry<String, String> row : noPopRows) {
                lines.add("- `" + row.getKey() + "`: " + row.getValue());
            }
        }
        lines.add("");
        return lines;
    }

    private static String blindSpotsHtml(ObjectNode blindSpots) {
        List<Map.Entry<String, String>> rows = blindSpotRows((ArrayNode) blindSpots.get("blind_spots"));
        List<Map.Entry<String, String>> noPopRows = noPopulationRows((ArrayNode) blindSpots.get("no_population"));
        String body;
        if (rows.isEmpty() && noPopRows.isEmpty()) {
            body = "<p>every check either has enough evidence, or nothing here would unlock more</p>";
        } else {
            StringBuilder items = new StringBuilder();
            for (Map.Entry<String, String> row : rows) {
                items.append("<li><strong>").append(esc(row.getKey())).append("</strong>: ")
                        .append(esc(row.getValue())).append("</li>");
            }
            StringBuilder b = new StringBuilder("<ul>").append(items).append("</ul>");
            if (!noPopRows.isEmpty()) {
                StringBuilder noPopItems = new StringBuilder();
                for (Map.Entry<String, String> row : noPopRows) {
                    noPopItems.append("<li><strong>").append(esc(row.getKey())).append("</strong>: ")
                            .append(esc(row.getValue())).append("</li>");
                }
                b.append("<h3>Records that don't show enough, with no single fix</h3><ul>")
                        .append(noPopItems).append("</ul>");
            }
            body = b.toString();
        }
        return "<section aria-labelledby=\"blind-spots\"><h2 id=\"blind-spots\">Where your records can't show it "
                + "yet</h2>" + body + "</section>";
    }

    /** The lines a command prints for {@code blindSpots}: the same node {@link #blindSpotsMd}/
     * {@link #blindSpotsHtml} render. */
    public static List<String> blindSpotsCliLines(ObjectNode blindSpots) {
        List<Map.Entry<String, String>> rows = blindSpotRows((ArrayNode) blindSpots.get("blind_spots"));
        List<Map.Entry<String, String>> noPopRows = noPopulationRows((ArrayNode) blindSpots.get("no_population"));
        List<String> lines = new ArrayList<>();
        lines.add("Where your records can't show it yet:");
        if (rows.isEmpty() && noPopRows.isEmpty()) {
            lines.add("  every check either has enough evidence, or nothing here would unlock more");
            return lines;
        }
        for (Map.Entry<String, String> row : rows) {
            lines.add("  " + row.getKey() + ": " + row.getValue());
        }
        if (!noPopRows.isEmpty()) {
            lines.add("  Records that don't show enough, with no single fix:");
            for (Map.Entry<String, String> row : noPopRows) {
                lines.add("    " + row.getKey() + ": " + row.getValue());
            }
        }
        return lines;
    }

    private static List<Assertions.Assertion> sortedBySubjectControl(List<Assertions.Assertion> assertions) {
        List<Assertions.Assertion> sorted = new ArrayList<>(assertions);
        sorted.sort((a, b) -> {
            int c = a.subject.compareTo(b.subject);
            return c != 0 ? c : a.control.compareTo(b.control);
        });
        return sorted;
    }

    /** {@code activity} feeds the "what your agents did" section that leads the report (18.4);
     * {@code blindSpots} feeds the not-enough-evidence section right after it (18.5); either may be
     * {@code null} for a bare re-render with neither available. */
    public static String renderReportMd(
            List<Assertions.Assertion> assertions, Map<String, Integer> counts, String language, ObjectNode activity,
            ObjectNode blindSpots) {
        Map<String, String> cat = Messages.catalogue(language);
        List<String> lines = new ArrayList<>();
        lines.add("# " + cat.get("report.title"));
        lines.add("");
        // The records lead the report (SPEC's evidence-first framing, 18.4): what happened, before
        // how it measures up. What the records can't show yet (18.5) comes right after.
        if (activity != null) {
            lines.addAll(activityMd(activity, cat));
        }
        if (blindSpots != null) {
            lines.addAll(blindSpotsMd(blindSpots));
        }
        Verdict.Summary verdict = Verdict.summarize(assertions);
        lines.add("## " + cat.get("report.verdict_heading"));
        lines.add("");
        lines.add("**" + cat.get("verdict." + verdict.verdict()) + "**");
        lines.add("");
        if (verdict.topGaps().isEmpty()) {
            lines.add(cat.get("report.top_gaps_heading") + ": " + cat.get("report.no_gaps"));
        } else {
            lines.add(cat.get("report.top_gaps_heading") + ":");
            lines.add("");
            for (Verdict.Gap gap : verdict.topGaps()) {
                lines.add("- " + Verdict.gapText(gap, cat));
            }
        }
        lines.add("");
        lines.add(cat.get("report.next_step_heading") + ": " + cat.get("next." + verdict.verdict()));
        lines.add("");
        lines.add("## " + cat.get("report.summary_heading"));
        lines.add("");
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            // List-item first content: backtick-wrapped (SPEC §7 injection hardening;
            // `contracts/P18-18.21.md`).
            lines.add("- `" + sanitizeForMarkdown(outcomeLabel(cat, e.getKey())) + "`: " + e.getValue());
        }
        lines.add("");
        lines.add("## " + cat.get("report.assertions_heading"));
        lines.add("");
        if (assertions.isEmpty()) {
            lines.add("_" + cat.get("report.no_controls") + "_");
        }
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            String control = sanitizeForMarkdown(a.control);
            String subject = sanitizeForMarkdown(a.subject);
            lines.add("- `" + control + "` @ `" + subject + "` -> **"
                    + sanitizeForMarkdown(outcomeLabel(cat, a.outcome)) + "** "
                    + "(rung " + a.rung + ", " + sanitizeForMarkdown(a.mode) + "; "
                    + a.population[1] + "/" + a.population[0] + " failed)");
            for (JsonNode entry : a.crosswalk) {
                // List-item first content: backtick-wrapped.
                lines.add("  - `" + sanitizeForMarkdown(crosswalkText(entry, cat)) + "`");
            }
        }
        return String.join("\n", lines) + "\n";
    }

    private static final String HTML_STYLE =
            "body{font-family:system-ui,sans-serif;margin:2rem;color:#111;background:#fff;line-height:1.5}"
                    + "h1{font-size:1.5rem}h2{font-size:1.2rem;margin-top:1.5rem}"
                    + "table{border-collapse:collapse;width:100%}"
                    + "th,td{border:1px solid #999;padding:.35rem .5rem;text-align:left}"
                    + "th{background:#f0f0f0}caption{text-align:left;font-weight:bold;margin-bottom:.5rem}"
                    + "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
                    + "@media print{@page{size:A4;margin:1.5cm}body{margin:0}@page :first{size:letter}"
                    + "table{page-break-inside:auto}tr{page-break-inside:avoid}}";

    private static String verdictHtml(Verdict.Summary summary, Map<String, String> cat) {
        String heading = esc(cat.get("report.top_gaps_heading"));
        String gaps;
        if (summary.topGaps().isEmpty()) {
            gaps = "<p>" + heading + ": " + esc(cat.get("report.no_gaps")) + "</p>";
        } else {
            StringBuilder items = new StringBuilder();
            for (Verdict.Gap gap : summary.topGaps()) {
                items.append("<li>").append(esc(Verdict.gapText(gap, cat))).append("</li>");
            }
            gaps = "<p>" + heading + ":</p><ul>" + items + "</ul>";
        }
        return "<section aria-labelledby=\"verdict\"><h2 id=\"verdict\">" + esc(cat.get("report.verdict_heading"))
                + "</h2><p><strong>" + esc(cat.get("verdict." + summary.verdict())) + "</strong></p>" + gaps
                + "<p>" + esc(cat.get("report.next_step_heading")) + ": " + esc(cat.get("next." + summary.verdict()))
                + "</p></section>";
    }

    /** {@code activity} feeds the "what your agents did" section that leads the report (18.4);
     * {@code blindSpots} feeds the not-enough-evidence section right after it (18.5); either may be
     * {@code null} for a bare re-render with neither available. */
    public static String renderReportHtml(
            List<Assertions.Assertion> assertions, Map<String, Integer> counts, String language, ObjectNode activity,
            ObjectNode blindSpots) {
        Map<String, String> cat = Messages.catalogue(language);
        String title = esc(cat.get("report.title"));
        StringBuilder summary = new StringBuilder();
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            summary.append("<li>").append(sanitizeForHtml(outcomeLabel(cat, e.getKey()))).append(": ").append(e.getValue()).append("</li>");
        }
        StringBuilder rows = new StringBuilder();
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            StringBuilder clauses = new StringBuilder();
            for (int i = 0; i < a.crosswalk.size(); i++) {
                if (i > 0) {
                    clauses.append("; ");
                }
                clauses.append(sanitizeForHtml(crosswalkText(a.crosswalk.get(i), cat)));
            }
            rows.append("<tr><td>").append(sanitizeForHtml(a.control)).append("</td><td>")
                    .append(sanitizeForHtml(a.subject)).append("</td>")
                    .append("<td>").append(sanitizeForHtml(outcomeLabel(cat, a.outcome))).append("</td>")
                    .append("<td>").append(clauses).append("</td></tr>");
        }
        String bodyRows = rows.length() > 0
                ? rows.toString()
                : "<tr><td colspan=\"4\">" + esc(cat.get("report.no_controls")) + "</td></tr>";
        return "<!doctype html><html lang=\"" + esc(language) + "\"><head><meta charset=\"utf-8\">"
                + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                + "<meta http-equiv=\"Content-Security-Policy\" "
                + "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'\">"
                + "<title>" + title + "</title><style>" + HTML_STYLE + "</style></head><body>"
                + "<main><h1>" + title + "</h1>"
                + (activity != null ? activityHtml(activity, cat) : "")
                + (blindSpots != null ? blindSpotsHtml(blindSpots) : "")
                + verdictHtml(Verdict.summarize(assertions), cat)
                + "<section aria-labelledby=\"summary\"><h2 id=\"summary\">"
                + esc(cat.get("report.summary_heading")) + "</h2><ul>" + summary + "</ul></section>"
                + "<section aria-labelledby=\"assertions\"><h2 id=\"assertions\">"
                + esc(cat.get("report.assertions_heading")) + "</h2>"
                + "<table><caption>" + esc(cat.get("report.assertions_heading")) + "</caption>"
                + "<thead><tr><th scope=\"col\">Control</th><th scope=\"col\">Subject</th>"
                + "<th scope=\"col\">Outcome</th><th scope=\"col\">Clause</th></tr></thead>"
                + "<tbody>" + bodyRows + "</tbody></table></section>"
                + "<footer><p>" + esc(cat.get("report.affected_persons")) + "</p></footer>"
                + "</main></body></html>\n";
    }

    /** A deterministic OSCAL date-time for this run: the earliest evidence-window start across the
     * assertions, so it reflects what was actually reviewed rather than the run's wall clock. A run
     * with no assertions falls back to a fixed epoch, never the wall clock. */
    private static String oscalTimestamp(List<Assertions.Assertion> assertions) {
        String earliest = null;
        for (Assertions.Assertion a : assertions) {
            String start = a.window[0];
            if (earliest == null || start.compareTo(earliest) < 0) {
                earliest = start;
            }
        }
        return earliest != null ? earliest : "1970-01-01T00:00:00Z";
    }

    /** Render {@code oscal-ar.json} as an importable NIST OSCAL 1.1.2 Assessment Results document
     * (SPEC §9, §9.4): every result carries real {@code observations[]} built from the assertion's
     * own evidence pointers, every finding resolves to the observation that backs it and links to its
     * real control id so a GRC platform can trace the finding to the requirement it assesses. A
     * faithful port of the Python reference; no catalog object is needed since the control id alone is
     * the traceable token. */
    public static ObjectNode renderOscal(List<Assertions.Assertion> assertions) {
        String when = oscalTimestamp(assertions);
        List<Assertions.Assertion> ordered = sortedBySubjectControl(assertions);

        ObjectNode root = Json.nodes().objectNode();
        ObjectNode ar = root.putObject("assessment-results");
        ar.put("uuid", uuid("assessment-results"));
        ObjectNode metadata = ar.putObject("metadata");
        metadata.put("title", "AgentCE Assessment Results");
        metadata.put("version", Version.ENGINE_VERSION);
        metadata.put("oscal-version", "1.1.2");
        metadata.put("last-modified", when);
        ar.putObject("import-ap").put("href", "urn:agentce:assessment-plan:structural");

        ArrayNode results = ar.putArray("results");
        ObjectNode result = results.addObject();
        result.put("uuid", uuid("result"));
        result.put("title", "AgentCE structural assessment");
        result.put(
                "description",
                "AgentCE's structural, statistical, and probe-based assessment of the run's "
                        + "subjects against the resolved catalog(s).");
        result.put("start", when);
        result.putObject("reviewed-controls")
                .putArray("control-selections")
                .addObject()
                .putObject("include-all");

        Map<String, String> observationUuid = new LinkedHashMap<>();
        ArrayNode observations = Json.nodes().arrayNode();
        for (Assertions.Assertion a : ordered) {
            String key = a.control + " " + a.subject;
            String obsUuid = uuid("observation", a.control, a.subject);
            observationUuid.put(key, obsUuid);
            ObjectNode observation = observations.addObject();
            observation.put("uuid", obsUuid);
            observation.put("description", "Assessment activity for " + a.control + " on " + a.subject + ".");
            observation.putArray("methods").add("TEST");
            observation.put("collected", when);
            if (!a.evidence.isEmpty()) {
                ArrayNode relevantEvidence = observation.putArray("relevant-evidence");
                for (Assertions.EvidencePointer e : a.evidence) {
                    relevantEvidence
                            .addObject()
                            .put("href", e.ref)
                            .put("description", e.sourceClass + " evidence, digest " + e.digest);
                }
            }
        }
        if (observations.size() > 0) {
            result.set("observations", observations);
        }

        ArrayNode findings = Json.nodes().arrayNode();
        for (Assertions.Assertion a : ordered) {
            String key = a.control + " " + a.subject;
            ObjectNode finding = findings.addObject();
            finding.put("uuid", uuid("finding", a.control, a.subject));
            finding.put("title", a.control + " for " + a.subject);
            finding.put("description", a.control + " assessed for " + a.subject + ": " + a.outcome + ".");
            ObjectNode target = finding.putObject("target");
            target.put("type", "objective-id");
            target.put("target-id", a.control);
            ObjectNode status = target.putObject("status");
            status.put("state", OSCAL_STATE.getOrDefault(a.outcome, "not-satisfied"));
            status.put("reason", a.outcome);
            finding.putArray("links")
                    .addObject()
                    .put("href", "urn:agentce:control:" + a.control)
                    .put("rel", "control");
            finding.putArray("related-observations")
                    .addObject()
                    .put("observation-uuid", observationUuid.get(key));
        }
        if (findings.size() > 0) {
            result.set("findings", findings);
        }

        return root;
    }

    /** Render {@code results.sarif} (SPEC §9) as a document a code-scanning consumer can actually use:
     * every rule carries catalog-sourced {@code name}/{@code help}/{@code helpUri}, every result carries
     * a synthetic {@code locations} entry (the subject is not a source file, so the location is a
     * stable pseudo-path for that subject) and a content-derived {@code partialFingerprints} (stable
     * across independent runs, so findings de-dup across scans), and {@code not_assessed} surfaces at a
     * level distinct from {@code insufficient_evidence} so a reader -- and a code-scanning gate -- can
     * tell unproven apart from thin evidence instead of one being silent. */
    public static ObjectNode renderSarif(List<Assertions.Assertion> assertions, List<Catalog> catalogs) {
        Map<String, Catalog.ControlSpec> byControl = controlIndex(catalogs);
        ObjectNode root = Json.nodes().objectNode();
        root.put("$schema", "https://agent-conformance.org/spec/report/results-sarif.schema.json");
        root.put("version", "2.1.0");
        ArrayNode runs = root.putArray("runs");
        ObjectNode run = runs.addObject();
        ObjectNode tool = run.putObject("tool");
        ObjectNode driver = tool.putObject("driver");
        driver.put("name", Version.ENGINE_NAME);
        driver.put("version", Version.ENGINE_VERSION);
        ArrayNode rules = driver.putArray("rules");
        TreeSet<String> controlIds = new TreeSet<>();
        for (Assertions.Assertion a : assertions) {
            controlIds.add(a.control);
        }
        for (String control : controlIds) {
            ObjectNode rule = rules.addObject();
            rule.put("id", control);
            rule.put("name", control);
            rule.putObject("help").put("text", sarifRuleHelpText(control, byControl.get(control)));
            rule.put("helpUri", sarifHelpUri(control));
        }
        ArrayNode results = run.putArray("results");
        for (Assertions.Assertion a : sortedBySubjectControl(assertions)) {
            if (SARIF_LEVEL.containsKey(a.outcome)) {
                ObjectNode r = results.addObject();
                r.put("ruleId", a.control);
                r.put("level", SARIF_LEVEL.get(a.outcome));
                r.putObject("message").put("text", a.control + " on " + a.subject + ": " + a.outcome);
                ObjectNode location = r.putArray("locations").addObject();
                location.putObject("physicalLocation")
                        .putObject("artifactLocation")
                        .put("uri", "agentce/subjects/" + safe(a.subject));
                r.putObject("partialFingerprints").put("agentceOutcomeHash/v1", sarifFingerprint(a));
            }
        }
        return root;
    }

    /** {@link #renderSarif(List, List)} for a bare re-render with no catalog objects available (SPEC
     * §9.4): still a valid, if less informative, SARIF document. */
    public static ObjectNode renderSarif(List<Assertions.Assertion> assertions) {
        return renderSarif(assertions, List.of());
    }

    public static ObjectNode renderEvidencePack(String subject, List<Assertions.Assertion> assertions, String role) {
        ObjectNode pack = Json.nodes().objectNode();
        pack.put("subject", subject);
        ArrayNode assertionsArr = pack.putArray("assertions");
        TreeSet<String> allEvidence = new TreeSet<>(Json::byteCompare);
        for (Assertions.Assertion a : assertions) {
            ObjectNode row = assertionsArr.addObject();
            row.put("control", a.control);
            row.put("outcome", a.outcome);
            row.put("mode", a.mode);
            TreeSet<String> refs = new TreeSet<>(Json::byteCompare);
            for (Assertions.EvidencePointer e : a.evidence) {
                refs.add(e.ref);
                allEvidence.add(e.ref);
            }
            ArrayNode evidence = row.putArray("evidence");
            refs.forEach(evidence::add);
            if (!a.crosswalk.isEmpty()) {
                ArrayNode crosswalk = row.putArray("crosswalk");
                a.crosswalk.forEach(crosswalk::add);
            }
        }
        ArrayNode packEvidence = pack.putArray("evidence");
        allEvidence.forEach(packEvidence::add);
        if (role != null) {
            pack.put("role", role);
        }
        return pack;
    }

    private static String now() {
        return ZonedDateTime.now(ZoneOffset.UTC).format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'"));
    }

    /** {@code catalogObjects} are the resolved catalog objects, matched to {@code catalogs} by
     * {@code id@version}, so each ref's digest is the catalog directory's real, recomputed-every-call
     * content digest (SPEC §14.5 CP-3) -- never read from a catalog's stored {@code provenance.digest}.
     * A label with no matching object here (a bare re-render that has only labels, no directories)
     * keeps the honest all-zero digest. Mirrors the TypeScript port's {@code buildManifest}. */
    public static ObjectNode buildManifest(
            String bundleDigest, List<String> catalogs, Map<String, String> outputs, String operator,
            List<String> invocation, List<String> supersedes, String reportLanguage, List<Catalog> catalogObjects) {
        Map<String, Catalog> byLabel = new LinkedHashMap<>();
        if (catalogObjects != null) {
            for (Catalog c : catalogObjects) {
                byLabel.put(c.id + "@" + c.version, c);
            }
        }
        String packageDigest = packageDigest();
        String host = Canonical.sha256Hex(
                (System.getProperty("os.name") + "|" + System.getProperty("os.arch") + "|" + packageDigest)
                        .getBytes(StandardCharsets.UTF_8));
        ObjectNode manifest = Json.nodes().objectNode();
        manifest.put("agentce_manifest_version", 1);
        ObjectNode engine = manifest.putObject("engine");
        engine.put("impl", Version.ENGINE_NAME);
        engine.put("version", Version.ENGINE_VERSION);
        engine.put("spec_version", Version.SPEC_VERSION);
        engine.put("package_digest", packageDigest);
        ObjectNode inputs = manifest.putObject("inputs");
        inputs.put("bundle_digest", bundleDigest);
        ArrayNode catalogRefs = inputs.putArray("catalogs");
        for (String entry : catalogs) {
            int at = entry.indexOf('@');
            String cid = at >= 0 ? entry.substring(0, at) : entry;
            String version = at >= 0 ? entry.substring(at + 1) : "";
            Catalog catalog = byLabel.get(entry);
            String digest = catalog != null && catalog.directory != null
                    ? Catalog.provenanceDigest(catalog.directory)
                    : ZERO_DIGEST;
            ObjectNode ref = catalogRefs.addObject();
            ref.put("id", cid);
            ref.put("version", version.isEmpty() ? "0" : version);
            ref.put("digest", digest);
        }
        ObjectNode outputsNode = manifest.putObject("outputs");
        List<String> outputKeys = new ArrayList<>(outputs.keySet());
        for (String key : outputKeys) {
            outputsNode.put(key, outputs.get(key));
        }
        ObjectNode runNode = manifest.putObject("run");
        runNode.put("started_at", now());
        runNode.put("operator", operator);
        runNode.put("host_fingerprint", "sha256:" + host);
        ArrayNode inv = runNode.putArray("invocation");
        invocation.forEach(inv::add);
        runNode.put("report_language", reportLanguage);
        if (!supersedes.isEmpty()) {
            ArrayNode sup = manifest.putArray("supersedes");
            supersedes.forEach(sup::add);
        }
        return manifest;
    }

    /** Write every report artifact for {@code assertions} and return the reproducibility manifest.
     * {@code catalogObjects} are the resolved catalog objects (not just their {@code id@version}
     * labels in {@code catalogs}), so {@code results.sarif} can carry catalog-sourced rule metadata
     * (SPEC §9); an empty list still yields a valid, if less informative, SARIF document.
     *
     * <p>{@code activity} is {@link Activity#summarizeActivity}'s node over the run's accepted events
     * and resolved profile (18.4), feeding {@code activity.json} and the "what your agents did"
     * report section; {@code blindSpots} is {@link BlindSpots#computeBlindSpots}'s node over
     * {@code assertions} and the same {@code profile}/{@code catalogObjects}/events (18.5), feeding
     * {@code blind-spots.json} and the not-enough-evidence report section. Both are computed by the
     * caller, once, since each is also needed for the terminal summary and the {@code --json}
     * envelope; {@code null} gets the honest answer for a caller with neither. */
    public static ObjectNode writeReport(
            Path outDir, List<Assertions.Assertion> assertions, String bundleDigest, List<String> catalogs,
            String operator, List<String> invocation, List<String> supersedes, String reportLanguage,
            List<Catalog> catalogObjects, ObjectNode activity, ObjectNode blindSpots) {
        Assertions.checkDc5(assertions);
        try {
            Files.createDirectories(outDir);
            Map<String, String> outputs = new LinkedHashMap<>();
            ObjectNode activityNode = activity != null ? activity : Activity.summarizeActivity(List.of(), new Profile());
            ObjectNode blindSpotsNode = blindSpots != null ? blindSpots : emptyBlindSpots();

            ArrayNode assertionsJson = Json.nodes().arrayNode();
            for (Assertions.Assertion a : assertions) {
                assertionsJson.add(a.toJson());
            }
            outputs.put("assertions.json", writeJson(outDir, "assertions.json", assertionsJson));
            outputs.put("activity.json", writeJson(outDir, "activity.json", activityNode));
            outputs.put("blind-spots.json", writeJson(outDir, "blind-spots.json", blindSpotsNode));

            Map<String, Integer> counts = Assertions.aggregate(assertions);
            outputs.put(
                    "report.md",
                    writeText(outDir, "report.md",
                            renderReportMd(assertions, counts, reportLanguage, activityNode, blindSpotsNode)));
            outputs.put(
                    "report.html",
                    writeText(outDir, "report.html",
                            renderReportHtml(assertions, counts, reportLanguage, activityNode, blindSpotsNode)));
            outputs.put("oscal-ar.json", writeJson(outDir, "oscal-ar.json", renderOscal(assertions)));
            outputs.put("results.sarif", writeJson(outDir, "results.sarif", renderSarif(assertions, catalogObjects)));

            TreeSet<String> subjects = new TreeSet<>(Json::byteCompare);
            for (Assertions.Assertion a : assertions) {
                subjects.add(a.subject);
            }
            for (String subject : subjects) {
                List<Assertions.Assertion> forSubject = new ArrayList<>();
                for (Assertions.Assertion a : assertions) {
                    if (a.subject.equals(subject)) {
                        forSubject.add(a);
                    }
                }
                ObjectNode pack = renderEvidencePack(subject, forSubject, null);
                String rel = "packs/" + safe(subject) + "/pack.json";
                byte[] data = Canonical.canonicalize(pack);
                Path path = outDir.resolve(rel);
                Files.createDirectories(path.getParent());
                Files.write(path, data);
                outputs.put(rel, digestBytes(data));
            }

            ObjectNode manifest = buildManifest(
                    bundleDigest, catalogs, outputs, operator, invocation, supersedes, reportLanguage, catalogObjects);
            Files.write(outDir.resolve("manifest.json"), Json.pretty(manifest).getBytes(StandardCharsets.UTF_8));
            return manifest;
        } catch (IOException e) {
            throw new IllegalStateException("cannot write report to " + outDir + ": " + e.getMessage(), e);
        }
    }

    /** The honest empty answer for a caller with no assertions to explain (never recomputed from an
     * empty {@link Profile}, unlike {@code activity}'s fallback -- an empty profile has zero
     * subjects, which would fail {@link BlindSpots#computeBlindSpots}'s positional pairing
     * immediately against any non-empty {@code assertions} list, RFC 0008 Sec.6). */
    private static ObjectNode emptyBlindSpots() {
        ObjectNode empty = Json.nodes().objectNode();
        empty.putArray("blind_spots");
        empty.putArray("no_population");
        return empty;
    }

    private static String writeJson(Path outDir, String name, JsonNode obj) throws IOException {
        byte[] data = Canonical.canonicalize(obj);
        Files.write(outDir.resolve(name), data);
        return digestBytes(data);
    }

    private static String writeText(Path outDir, String name, String text) throws IOException {
        byte[] data = text.getBytes(StandardCharsets.UTF_8);
        Files.write(outDir.resolve(name), data);
        return digestBytes(data);
    }
}
