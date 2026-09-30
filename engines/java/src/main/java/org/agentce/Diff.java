package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.TreeSet;

/**
 * {@code agentce diff} (SPEC §9.3): a real, content-keyed delta between two assertion sets -- every
 * {@code (control, subject)} whose outcome changed, was added, or was removed -- keyed by identity,
 * never array position, so the result is independent of either input's element order. A port of the
 * Python reference's {@code _diff_assertion_sets}/{@code _diff_field}/{@code _classify_change}/
 * {@code _what_changed}/{@code _diff_change_line}/{@code _diff_md_lines}
 * ({@code commands/__init__.py:2282-2424}), mirroring {@code diff.ts}'s TypeScript port.
 */
public final class Diff {
    private Diff() {}

    /** {@code agentce diff --format}'s three renderings (SPEC §9.3). */
    public static final List<String> DIFF_FORMATS = List.of("text", "json", "md");

    /** {@code from}/{@code to} are {@code null} for an assertion present on only one side. */
    public record Change(String control, String subject, String from, String to) {}

    private record Key(String control, String subject) {}

    private static final Comparator<Key> KEY_ORDER = Comparator.comparing(Key::control, Json::byteCompare)
            .thenComparing(Key::subject, Json::byteCompare);

    /** {@code control}/{@code subject}/{@code outcome} read off an arbitrary record; throws {@code
     * input.diff_field_not_string} when the field is present but not a JSON textual node (a disclosed,
     * intentionally narrower divergence from Python's silent {@code str()} coercion -- TRADEOFFS.md). A
     * structurally-missing field throws {@link IllegalArgumentException}, mirroring Python's uncaught
     * {@code KeyError} -- a pre-existing, accepted gap shared by every other command's malformed-input
     * path in this engine, not introduced here. */
    private static String stringField(JsonNode entry, String field) {
        if (!entry.has(field)) {
            throw new IllegalArgumentException("missing required field '" + field + "'");
        }
        JsonNode value = entry.get(field);
        if (!value.isTextual()) {
            throw new InputError(
                    "input.diff_field_not_string",
                    "assertion field '" + field + "' must be a string, not " + Json.pretty(value) + ".",
                    "emit control/subject/outcome as JSON strings.");
        }
        return value.textValue();
    }

    /** {@code entries} keyed by {@code (control, subject)} identity -- a real tuple key ({@code
     * record Key}), never a composite string, so two distinct pairs can never collide. A repeated
     * {@code (control, subject)} pair within one side's own array resolves last-write-wins (mirrors a
     * Python dict literal's own key-collision rule). */
    private static Map<Key, String> bySide(ArrayNode entries) {
        Map<Key, String> out = new LinkedHashMap<>();
        for (JsonNode entry : entries) {
            String control = stringField(entry, "control");
            String subject = stringField(entry, "subject");
            String outcome = stringField(entry, "outcome");
            out.put(new Key(control, subject), outcome);
        }
        return out;
    }

    /** A real, content-keyed delta between two assertion sets, sorted by {@code (control, subject)}:
     * {@code byteCompare(control)} then, on a tie, {@code byteCompare(subject)} -- real tuple
     * comparison, never a composite-string sort. */
    public static List<Change> diffAssertionSets(ArrayNode a, ArrayNode b) {
        Map<Key, String> left = bySide(a);
        Map<Key, String> right = bySide(b);
        TreeSet<Key> keys = new TreeSet<>(KEY_ORDER);
        keys.addAll(left.keySet());
        keys.addAll(right.keySet());
        List<Change> changes = new ArrayList<>();
        for (Key key : keys) {
            String before = left.get(key);
            String after = right.get(key);
            if (!Objects.equals(before, after)) {
                changes.add(new Change(key.control(), key.subject(), before, after));
            }
        }
        return changes;
    }

    /** {@code value} sanitised for the terminal, or the fixed literal {@code "(none)"} when the key
     * was absent on one side of the diff (an added or removed assertion) -- {@code null} is never
     * passed into the sanitiser, whose own empty-value substitute means something different. */
    private static String diffField(String value) {
        return value != null ? Report.sanitizeForMarkdown(value) : "(none)";
    }

    /** One of {@code "closed"}, {@code "opened"}, {@code "other"} for a single {@code (before,
     * after)} outcome pair (SPEC §9.3; item 18.6): {@code "closed"} iff {@code before} was a gap
     * ({@link Verdict#GAP_OUTCOMES}, which already includes {@code not_assessed}) and {@code after}
     * is {@code "conformant"}; {@code "opened"} iff the reverse. An added or removed assertion
     * (either side {@code null}) and any pair touching {@code "not_applicable"} always land in
     * {@code "other"}, as does an out-of-vocabulary outcome string. */
    public static String classifyChange(String before, String after) {
        if (before != null && Verdict.GAP_OUTCOMES.contains(before) && "conformant".equals(after)) {
            return "closed";
        }
        if ("conformant".equals(before) && after != null && Verdict.GAP_OUTCOMES.contains(after)) {
            return "opened";
        }
        return "other";
    }

    /** Group {@code changes} ({@link #diffAssertionSets}'s own output, already sorted by {@code
     * (control, subject)}) by {@link #classifyChange}, preserving that order within each of the
     * three groups -- always all three keys present, even when empty. */
    public static Map<String, List<Change>> whatChanged(List<Change> changes) {
        Map<String, List<Change>> grouped = new LinkedHashMap<>();
        grouped.put("closed", new ArrayList<>());
        grouped.put("opened", new ArrayList<>());
        grouped.put("other", new ArrayList<>());
        for (Change change : changes) {
            grouped.get(classifyChange(change.from(), change.to())).add(change);
        }
        return grouped;
    }

    /** {@code control @ subject: from -> to}, sanitised -- the one line both the text and md formats
     * render for a single change. */
    public static String diffChangeLine(Change change) {
        String control = Report.sanitizeForMarkdown(change.control());
        String subject = Report.sanitizeForMarkdown(change.subject());
        return control + " @ " + subject + ": " + diffField(change.from()) + " -> " + diffField(change.to());
    }

    /**
     * The echoed {@code report_a}/{@code report_b} path, normalized the way Python's {@code
     * pathlib.Path.__str__} renders a pure path: split on {@code /}, drop empty and {@code .}
     * segments, keep every {@code ..} unchanged, keep a leading {@code /} if the input had one, and
     * render an all-dropped result as {@code .}. Deliberately <b>not</b> {@code
     * Paths.get(raw).normalize()}, which wrongly collapses {@code ..} segments and so disagrees with
     * Python's own rendering.
     */
    public static String normalizePosixPath(String raw) {
        boolean absolute = raw.startsWith("/");
        List<String> segments = new ArrayList<>();
        for (String segment : raw.split("/")) {
            if (!segment.isEmpty() && !segment.equals(".")) {
                segments.add(segment);
            }
        }
        String joined = String.join("/", segments);
        if (absolute) {
            return "/" + joined;
        }
        return joined.isEmpty() ? "." : joined;
    }

    /** The {@code --format md} "## What changed" section as a list of lines: one {@code ### <Label>
     * (<n>)} subsection per <b>non-empty</b> group only, in {@code closed, opened, other} order, each
     * a bullet list built from the same fields the text format already renders. */
    public static List<String> diffMdLines(Map<String, List<Change>> grouped) {
        List<Map.Entry<String, String>> groups =
                List.of(Map.entry("closed", "Closed"), Map.entry("opened", "Opened"), Map.entry("other", "Other changes"));
        List<Map.Entry<String, String>> nonEmpty = new ArrayList<>();
        for (Map.Entry<String, String> g : groups) {
            if (!grouped.get(g.getKey()).isEmpty()) {
                nonEmpty.add(g);
            }
        }
        if (nonEmpty.isEmpty()) {
            return List.of("## What changed", "", "no differences.");
        }
        List<String> lines = new ArrayList<>();
        lines.add("## What changed");
        for (Map.Entry<String, String> g : nonEmpty) {
            lines.add("");
            lines.add("### " + g.getValue() + " (" + grouped.get(g.getKey()).size() + ")");
            for (Change change : grouped.get(g.getKey())) {
                lines.add("- " + diffChangeLine(change));
            }
        }
        return lines;
    }
}
