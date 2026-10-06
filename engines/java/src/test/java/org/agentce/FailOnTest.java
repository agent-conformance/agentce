package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * The {@code --fail-on} expression language (18.73) is a port of {@code agentce/fail_on.py}: these
 * tests pin what it accepts and how it matches, and every refusal's cause, copied from the real Python
 * output of {@code parse_fail_on} for the same expression.
 */
class FailOnTest {
    private static final String FIX = "use comparisons of the form field==\"literal\" joined by and/or, over: "
            + "control, family, mode, outcome, rung, severity, subject.";

    private static Assertions.Assertion assertion(String control, String subject, String outcome, String severity) {
        Assertions.Assertion a = new Assertions.Assertion();
        a.control = control;
        a.controlVersion = "2026.09";
        a.subject = subject;
        a.outcome = outcome;
        a.rung = 0;
        a.mode = "manual";
        a.severity = severity;
        a.family = control.substring(0, 3);
        return a;
    }

    private static final Assertions.Assertion AUV04 =
            assertion("AUV-04", "spiffe://corp/agents/auditor-view-fixture", "non-conformant", "high");
    private static final Assertions.Assertion AUV01 =
            assertion("AUV-01", "spiffe://corp/agents/auditor-view-fixture", "non-conformant", "high");

    private static boolean matches(String expr, Assertions.Assertion a) {
        return FailOn.parse(expr).matches(a);
    }

    @Test
    void parseAcceptsWhatPythonAccepts() {
        assertTrue(matches("outcome==\"non-conformant\" and severity==\"high\"", AUV04)); // F1
        assertFalse(matches("severity==\"critical\"", AUV04)); // F2
        // and binds tighter than or (F4): AUV-04 matches the first group, AUV-01 high neither.
        String f4 = "control==\"AUV-04\" or control==\"AUV-01\" and severity==\"low\"";
        assertTrue(matches(f4, AUV04));
        assertFalse(matches(f4, AUV01));
        assertTrue(matches("rung==\"0\" and mode==\"manual\" and family==\"AUV\"", AUV04)); // F5
        // single quotes, and a backslash that keeps the next character as-is (F6)
        assertTrue(matches("subject=='spiffe://corp/agents/auditor-view-fixture' and control=='AUV-0\\4'", AUV04));
        // Python's whitespace, including U+001C, U+3000 and U+0085 (F12)
        assertTrue(matches("\u001c severity ==　\"high\"\t\n\u0085", AUV04));
        assertFalse(matches("control==\"é\"", AUV04)); // F13
        assertFalse(matches("rung==\"2.0\"", AUV04)); // F14: rung compares as its decimal string
        assertTrue(matches("rung==\"0\"", AUV04));
        // a quote of the other kind, and an astral code point, inside a literal
        Assertions.Assertion odd = assertion("AUV-09", "😀", "partial", "low");
        assertTrue(matches("subject==\"😀\"", odd));
        assertTrue(matches("control=='AUV-09' and outcome==\"partial\" or subject=='\"'", odd));
        assertFalse(matches("subject=='\"'", odd));
        // a null field never equals a literal, as Python's None == "x" is False
        Assertions.Assertion noMode = assertion("AUV-05", "s", "conformant", "low");
        noMode.mode = null;
        assertFalse(matches("mode==\"manual\"", noMode));
        assertTrue(matches("mode==\"manual\" or control==\"AUV-05\"", noMode));
    }

    @Test
    void parseRefusesWithPythonCause() {
        String[][] cases = {
                {"", "--fail-on '' is not a valid expression: the --fail-on expression is empty"}, // R1
                {"", "--fail-on '' is not a valid expression: the --fail-on expression is empty"}, // R2
                {"   ", "--fail-on '   ' is not a valid expression: the --fail-on expression is empty"}, // R3
                {"foo==\"x\"", "--fail-on 'foo==\"x\"' is not a valid expression: unknown field 'foo'; choose from: control, family, mode, outcome, rung, severity, subject"}, // R4
                {"and==\"x\"", "--fail-on 'and==\"x\"' is not a valid expression: expected a field name, got the reserved word 'and'"}, // R5
                {"outcome \"x\"", "--fail-on 'outcome \"x\"' is not a valid expression: expected '==' after field name 'outcome'"}, // R6
                {"outcome=\"x\"", "--fail-on 'outcome=\"x\"' is not a valid expression: unexpected character '=' at position 7"}, // R7
                {"outcome==x", "--fail-on 'outcome==x' is not a valid expression: expected a quoted string literal after '=='"}, // R8
                {"outcome==\"x", "--fail-on 'outcome==\"x' is not a valid expression: unterminated string literal starting at position 9"}, // R9
                {"outcome==\"x\" severity", "--fail-on 'outcome==\"x\" severity' is not a valid expression: unexpected token 'severity' after a complete expression"}, // R10
                {"__import__(\"os\").system(\"id\")", "--fail-on '__import__(\"os\").system(\"id\")' is not a valid expression: unexpected character '(' at position 10"}, // R11
                {"outcome==`id`", "--fail-on 'outcome==`id`' is not a valid expression: unexpected character '`' at position 9"}, // R12
                {"outcome==\"x\" or os.system(\"id\")", "--fail-on 'outcome==\"x\" or os.system(\"id\")' is not a valid expression: unexpected character '.' at position 18"}, // R13
                {"(outcome==\"x\")", "--fail-on '(outcome==\"x\")' is not a valid expression: unexpected character '(' at position 0"}, // R14
                {"outcom\u00e9==\"x\"", "--fail-on 'outcom\u00e9==\"x\"' is not a valid expression: unknown field 'outcom\u00e9'; choose from: control, family, mode, outcome, rung, severity, subject"}, // R15
                {"\ud83d\ude00", "--fail-on '\ud83d\ude00' is not a valid expression: unexpected character '\ud83d\ude00' at position 0"}, // R16
                {"outcome==\"x\"\u0001", "--fail-on 'outcome==\"x\"\\x01' is not a valid expression: unexpected character '\\x01' at position 12"}, // R17
                {"\u200b", "--fail-on '\\u200b' is not a valid expression: unexpected character '\\u200b' at position 0"}, // R18
                {"\"\ud83d\ude00\" \u0001", "--fail-on '\"\ud83d\ude00\" \\x01' is not a valid expression: unexpected character '\\x01' at position 4"}, // R19
                {"outcome==\"x\" and", "--fail-on 'outcome==\"x\" and' is not a valid expression: expected a field name"}, // R20
                {"==", "--fail-on '==' is not a valid expression: expected a field name"}, // R21
                {"outcome==", "--fail-on 'outcome==' is not a valid expression: expected a quoted string literal after '=='"}, // R22
                {"1outcome==\"x\"", "--fail-on '1outcome==\"x\"' is not a valid expression: unexpected character '1' at position 0"}, // R23
                {"a\u00b2==\"x\"", "--fail-on 'a\u00b2==\"x\"' is not a valid expression: unknown field 'a\u00b2'; choose from: control, family, mode, outcome, rung, severity, subject"}, // R24
                {"outcome==\"x\\", "--fail-on 'outcome==\"x\\\\' is not a valid expression: unterminated string literal starting at position 9"}, // R25
                {"it's", "--fail-on \"it's\" is not a valid expression: unterminated string literal starting at position 2"}, // R26
                {"outcome===\"x\"", "--fail-on 'outcome===\"x\"' is not a valid expression: unexpected character '=' at position 9"}, // R27
                {"\"x\"==\"x\"", "--fail-on '\"x\"==\"x\"' is not a valid expression: expected a field name"}, // R28
                {"outcome==\"\\U0001F600\"\\U0001F600", "--fail-on 'outcome==\"\\\\U0001F600\"\\\\U0001F600' is not a valid expression: unexpected character '\\\\' at position 21"}, // R29
                {"Outcome==\"x\"", "--fail-on 'Outcome==\"x\"' is not a valid expression: unknown field 'Outcome'; choose from: control, family, mode, outcome, rung, severity, subject"}, // R30
                {"outcome==\"x\" OR severity==\"y\"", "--fail-on 'outcome==\"x\" OR severity==\"y\"' is not a valid expression: unexpected token 'OR' after a complete expression"}, // R31
        };
        for (String[] c : cases) {
            InputError err = assertThrows(InputError.class, () -> FailOn.parse(c[0]), c[0]);
            assertEquals("input.fail_on_invalid_expression", err.key, c[0]);
            assertEquals(c[1], err.reason, c[0]);
            assertEquals(FIX, err.fix, c[0]);
            assertEquals(ExitCode.INPUT_ERROR.code, err.exitCode, c[0]);
        }
    }

    @Test
    void longChainEvaluatesWithoutRecursion() {
        // D1/D2: a 3000-clause or chain, and a 3000-clause and chain, parse and evaluate in a loop.
        List<String> clauses = new ArrayList<>(Collections.nCopies(2999, "control==\"x\""));
        assertFalse(matches(String.join(" or ", clauses) + " or control==\"x\"", AUV01));
        assertTrue(matches(String.join(" or ", clauses) + " or control==\"AUV-01\"", AUV01));
        List<String> ands = new ArrayList<>(Collections.nCopies(2999, "family==\"AUV\""));
        assertTrue(matches(String.join(" and ", ands) + " and control==\"AUV-01\"", AUV01));
        assertFalse(matches(String.join(" and ", ands) + " and control==\"AUV-04\"", AUV01));
        assertEquals(3000, FailOn.parse(String.join(" or ", clauses) + " or control==\"x\"").groups().size());
    }

    @Test
    void whitespaceIsPythonIsspace() {
        // str.isspace in Python 3.12 is true for exactly these 29 code points.
        List<Integer> expected = new ArrayList<>();
        for (int cp = 0x09; cp <= 0x0D; cp++) {
            expected.add(cp);
        }
        for (int cp = 0x1C; cp <= 0x20; cp++) {
            expected.add(cp);
        }
        expected.addAll(List.of(0x85, 0xA0, 0x1680));
        for (int cp = 0x2000; cp <= 0x200A; cp++) {
            expected.add(cp);
        }
        expected.addAll(List.of(0x2028, 0x2029, 0x202F, 0x205F, 0x3000));
        assertEquals(29, expected.size());
        List<Integer> actual = new ArrayList<>();
        for (int cp = 0; cp <= Character.MAX_CODE_POINT; cp++) {
            if (FailOn.isPySpace(cp)) {
                actual.add(cp);
            }
        }
        assertEquals(expected, actual);
        // every one separates tokens in a real expression
        for (int cp : expected) {
            String ws = new String(Character.toChars(cp));
            assertTrue(matches(ws + "control" + ws + "==" + ws + "\"AUV-01\"" + ws + "or" + ws + "rung==\"9\"" + ws, AUV01));
        }
        // characters the JVM or other runtimes call whitespace-like but Python does not are refused
        String[][] notSpace = {
            {"​", "'\\u200b'"}, // ZERO WIDTH SPACE (Cf)
            {"﻿", "'\\ufeff'"}, // BYTE ORDER MARK (Cf)
            {"᠎", "'\\u180e'"}, // MONGOLIAN VOWEL SEPARATOR (Cf since Unicode 6.3)
            {"\u0000", "'\\x00'"},
        };
        for (String[] c : notSpace) {
            InputError err = assertThrows(InputError.class, () -> FailOn.parse("rung==\"0\"" + c[0]));
            assertTrue(err.reason.endsWith("unexpected character " + c[1] + " at position 9"), err.reason);
        }
    }
}
