package org.agentce;

import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;

/**
 * The common exit-code scheme for every AgentCE command (SPEC §8.5): {@code 0} success, {@code 1}
 * findings requiring action, {@code 2} insufficient evidence on a high-severity control, {@code 3}
 * input/version/verification error. When several apply the highest is returned and the JSON carries
 * all of them.
 */
public enum ExitCode {
    OK(0, "ok"),
    FINDINGS(1, "findings"),
    INSUFFICIENT_EVIDENCE(2, "insufficient_evidence"),
    INPUT_ERROR(3, "input_error");

    public final int code;
    public final String label;

    ExitCode(int code, String label) {
        this.code = code;
        this.label = label;
    }

    private static ExitCode of(int code) {
        for (ExitCode e : values()) {
            if (e.code == code) {
                return e;
            }
        }
        throw new IllegalArgumentException("unknown exit code " + code);
    }

    /** The stable name of a code, or throw for an unknown code. */
    public static String nameOf(int code) {
        return of(code).label;
    }

    /** The highest applicable code; an empty set means OK (SPEC §8.5). */
    public static int combine(Iterable<Integer> codes) {
        int highest = OK.code;
        for (int code : codes) {
            of(code);
            highest = Math.max(highest, code);
        }
        return highest;
    }

    /** The sorted, de-duplicated applicable codes; OK only when nothing else applies. */
    public static List<Integer> applicable(Iterable<Integer> codes) {
        TreeSet<Integer> unique = new TreeSet<>();
        for (int code : codes) {
            of(code);
            if (code != OK.code) {
                unique.add(code);
            }
        }
        return unique.isEmpty() ? List.of(OK.code) : new ArrayList<>(unique);
    }
}
