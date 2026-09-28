package org.agentce;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * The run verdict: one categorical state, the six-outcome tally, and the top gaps (SPEC §9.2, §9.3). The
 * verdict is derived from the assertions alone and is deliberately not a score — SPEC §9.2 forbids a single
 * composite number. Mirrors {@code agentce.verdict} in the reference engine.
 */
public final class Verdict {
    private Verdict() {}

    public static final String NON_CONFORMANT = "non-conformant";
    public static final String INCOMPLETE = "incomplete";
    public static final String CONFORMANT = "conformant";

    /** Outcomes that count as a gap, most urgent first. */
    static final List<String> GAP_OUTCOMES = List.of("non-conformant", "partial", "insufficient_evidence", "not_assessed");

    /** How many controls a gap line names before it says how many more there are. */
    static final int MAX_LISTED = 5;

    /** The controls of one gap outcome: up to {@link #MAX_LISTED} ids in order, and how many more. */
    public record Gap(String outcome, List<String> controls, int more) {}

    public record Summary(String verdict, Map<String, Integer> counts, List<Gap> topGaps) {}

    public static Summary summarize(List<Assertions.Assertion> assertions) {
        Map<String, Integer> counts = Assertions.aggregate(assertions);
        String verdict;
        if (counts.get("non-conformant") > 0) {
            verdict = NON_CONFORMANT;
        } else if (counts.get("partial") > 0 || counts.get("insufficient_evidence") > 0 || counts.get("not_assessed") > 0) {
            verdict = INCOMPLETE;
        } else {
            verdict = CONFORMANT;
        }
        List<Gap> gaps = new ArrayList<>();
        for (String outcome : GAP_OUTCOMES) {
            TreeSet<String> controls = new TreeSet<>();
            for (Assertions.Assertion a : assertions) {
                if (a.outcome.equals(outcome)) {
                    controls.add(a.control);
                }
            }
            if (!controls.isEmpty()) {
                List<String> all = new ArrayList<>(controls);
                gaps.add(new Gap(outcome, all.subList(0, Math.min(MAX_LISTED, all.size())), Math.max(0, all.size() - MAX_LISTED)));
            }
        }
        return new Summary(verdict, counts, gaps);
    }

    private static final Pattern PLURAL_RE = Pattern.compile("^\\{n,\\s*plural,\\s*(.*)\\}$", Pattern.DOTALL);
    private static final Pattern CATEGORY_RE = Pattern.compile("([A-Za-z0-9_=]+)\\s*\\{([^{}]*)\\}");

    /** English CLDR plural category: {@code "one"} for exactly 1, {@code "other"} otherwise -- the one
     * plural rule the message catalogue's {@code report.gaps_more} needs (mirrors the Python
     * reference's {@code i18n_format._english_plural_category} and the TypeScript port's
     * {@code pluralCategory}). */
    private static String pluralCategory(int n) {
        return n == 1 ? "one" : "other";
    }

    /** Render an ICU MessageFormat plural template ({@code "{n, plural, one {...} other {...}}"})
     * against {@code n}, replacing {@code #} with the formatted count -- a minimal port of the one
     * construct the message catalogue actually uses this way ({@code report.gaps_more}), not a general
     * ICU engine (mirrors the Python reference's {@code i18n_format.format_message} and the TypeScript
     * port's {@code formatPlural}). */
    private static String formatPlural(String template, int n) {
        Matcher plural = PLURAL_RE.matcher(template);
        if (!plural.matches()) {
            return template;
        }
        Map<String, String> categories = new LinkedHashMap<>();
        Matcher category = CATEGORY_RE.matcher(plural.group(1));
        while (category.find()) {
            categories.put(category.group(1), category.group(2));
        }
        String exact = "=" + n;
        String key = categories.containsKey(exact) ? exact : pluralCategory(n);
        String chosen = categories.getOrDefault(key, categories.getOrDefault("other", ""));
        return chosen.replace("#", String.valueOf(n));
    }

    /** One gap as text: {@code insufficient evidence: DAT-01, DAT-02 (+14 more gaps)}. Each control id
     * is sanitised (SPEC §7 injection hardening; {@code contracts/P18-18.21.md}): mid-line, after the
     * fixed label prefix, so no backtick-wrap is needed here (unlike the summary tally). */
    public static String gapText(Gap gap, Map<String, String> cat) {
        String label = cat.getOrDefault("outcome." + gap.outcome(), gap.outcome());
        String controls = gap.controls().stream().map(Report::sanitizeForMarkdown)
                .collect(Collectors.joining(", "));
        String text = label + ": " + controls;
        if (gap.more() > 0) {
            text += " (" + formatPlural(cat.get("report.gaps_more"), gap.more()) + ")";
        }
        return text;
    }
}
