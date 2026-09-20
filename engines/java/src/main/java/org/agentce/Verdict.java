package org.agentce;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

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

    /** One gap as text: {@code insufficient evidence: DAT-01, DAT-02 (+14 more)}. */
    public static String gapText(Gap gap, Map<String, String> cat) {
        String label = cat.getOrDefault("outcome." + gap.outcome(), gap.outcome());
        String text = label + ": " + String.join(", ", gap.controls());
        if (gap.more() > 0) {
            text += " (" + cat.get("report.gaps_more").replace("{n}", String.valueOf(gap.more())) + ")";
        }
        return text;
    }
}
