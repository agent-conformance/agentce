package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Every branch of {@link Argv#scan}, against a small grammar and against assess's own, each answer
 * checked against what Python's argparse gives the same argv (18.109). */
class ArgvTest {
    private static final Argv.Grammar G = grammar();

    private static Argv.Grammar grammar() {
        Map<String, Argv.Kind> options = new LinkedHashMap<>();
        options.put("-h", Argv.Kind.HELP);
        options.put("--help", Argv.Kind.HELP);
        options.put("--json", Argv.Kind.FLAG);
        options.put("--out", Argv.Kind.VALUE);
        options.put("--emit", Argv.Kind.VALUE);
        options.put("--dir", Argv.Kind.APPEND);
        return new Argv.Grammar(
                options, 1, "input.t_unrecognized_flag", "input.t_flag_needs_value", "FLAG-FIX", "EXTRA-FIX",
                Map.of("--emit", "<formats>"));
    }

    private static Argv.Result scan(String... argv) {
        return Argv.scan(argv, G);
    }

    private static InputError refused(String... argv) {
        return assertThrows(InputError.class, () -> scan(argv));
    }

    private static void assertRefused(String key, String cause, String fix, String... argv) {
        InputError err = refused(argv);
        assertEquals(key, err.key, String.join(" ", argv));
        assertEquals(cause, err.reason, String.join(" ", argv));
        assertEquals(fix, err.fix, String.join(" ", argv));
    }

    private static void assertUnknownFlag(String token, String... argv) {
        assertRefused("input.t_unrecognized_flag", "unrecognized flag '" + token + "'.", "FLAG-FIX", argv);
    }

    private static void assertNeedsValue(String flag, String hint, String... argv) {
        assertRefused("input.t_flag_needs_value", "argument " + flag + ": expected one argument",
                "pass " + flag + " " + hint + ".", argv);
    }

    @Test
    void anEmptyArgvReadsNothing() {
        Argv.Result r = scan();
        assertFalse(r.help());
        assertEquals(Map.of(), r.values());
        assertEquals(List.of(), r.positionals());
        assertEquals(List.of(), r.list("--dir"));
        assertFalse(r.flag("--json"));
    }

    @Test
    void aShortClusterIsRefusedFirstAnywhereBeforeTheSeparator() {
        assertUnknownFlag("-hx", "-hx");
        assertUnknownFlag("-hx", "-h", "-hx"); // before -h is reached
        assertUnknownFlag("-hx", "--zz", "--out", "-1", "-hx"); // before the deferred --zz
        assertUnknownFlag("-h=x", "-h=x");
        assertUnknownFlag("-x y", "-x y");
        assertUnknownFlag("-hx", "--out", "o", "--out=-hx", "-hx");
        // after `--` it is a positional; as an option's value it is the value
        assertEquals(List.of("-hx"), scan("--", "-hx").positionals());
        // a negative number is no cluster
        assertEquals(List.of("-12"), scan("-12").positionals());
    }

    @Test
    void theClusterPreScanStopsWhereAValueIsMissingSoTheMissingValueIsNamedFirst() {
        assertNeedsValue("--out", "<value>", "--out", "-hx");
        assertNeedsValue("--out", "<value>", "--out", "-xy");
    }

    @Test
    void aKnownNameIsAnOptionAndItsValueIsTheNextToken() {
        Argv.Result r = scan("--out", "o", "--json", "--dir", "a");
        assertEquals("o", r.value("--out"));
        assertTrue(r.flag("--json"));
        assertEquals(List.of("a"), r.list("--dir"));
    }

    @Test
    void theEqualsFormGivesAValueToEveryValueOption() {
        Argv.Result r = scan("--out=o", "--emit=", "--dir=a=b");
        assertEquals("o", r.value("--out"));
        assertEquals("", r.value("--emit"));
        assertEquals(List.of("a=b"), r.list("--dir"));
        assertEquals("-1", scan("--out=-1").value("--out"));
    }

    @Test
    void aValueOptionTakesADashTokenThatIsNotOptionLike() {
        assertEquals("-", scan("--out", "-").value("--out"));
        assertEquals("-1", scan("--out", "-1").value("--out"));
        assertEquals("-.5", scan("--out", "-.5").value("--out"));
        assertEquals("--x y", scan("--out", "--x y").value("--out"));
        assertEquals("-x y", scan("--out", "-x y").value("--out"));
    }

    @Test
    void theLastValueWinsAndAnAppendKeepsEveryValueInOrder() {
        Argv.Result r = scan("--out", "a", "--out=b", "--dir", "x", "--dir=y", "--dir", "z");
        assertEquals("b", r.value("--out"));
        assertEquals(List.of("x", "y", "z"), r.list("--dir"));
    }

    @Test
    void aMissingValueAtTheEndIsRefusedAtOnce() {
        assertNeedsValue("--out", "<value>", "--out");
        assertNeedsValue("--emit", "<formats>", "--emit");
        assertNeedsValue("--dir", "<value>", "--dir");
    }

    @Test
    void aMissingValueBeforeAnOptionIsRefusedAtOnce() {
        assertNeedsValue("--out", "<value>", "--out", "--json");
        assertNeedsValue("--out", "<value>", "--out", "-h");
        assertNeedsValue("--out", "<value>", "--out", "--zz");
        assertNeedsValue("--out", "<value>", "--out", "--emit=md");
        assertNeedsValue("--out", "<value>", "--out", "-h y"); // a short option with text joined to it
        // at once: before a deferred unknown flag and before a later -h
        assertNeedsValue("--out", "<value>", "--zz", "--out", "--json", "-h");
    }

    @Test
    void aMissingValueBeforeTheSeparatorIsRefusedAtOnce() {
        assertNeedsValue("--out", "<value>", "--out", "--", "o");
    }

    @Test
    void helpReturnsAtOnceWhereverItSitsBeforeTheSeparator() {
        assertTrue(scan("-h").help());
        assertTrue(scan("--help").help());
        assertTrue(scan("--out", "o", "-h", "--zz").help());
        assertTrue(scan("--zz", "a", "b", "--help").help()); // deferred refusals never fire
        assertTrue(scan("-h", "--json=1").help()); // nothing after help is read
        // after `--`, -h is a positional
        Argv.Result r = scan("--", "-h");
        assertFalse(r.help());
        assertEquals(List.of("-h"), r.positionals());
    }

    @Test
    void aFlagWithAValueIsRefusedAtOnce() {
        assertRefused("input.t_unrecognized_flag", "flag '--json' takes no value.", "drop the value: --json.",
                "--json=1");
        assertRefused("input.t_unrecognized_flag", "flag '--json' takes no value.", "drop the value: --json.",
                "--zz", "--json=");
    }

    @Test
    void helpWithAValueIsRefusedInArgparsesWords() {
        assertRefused("input.t_unrecognized_flag", "argument -h/--help: ignored explicit argument 'x'.",
                "FLAG-FIX", "--help=x");
        assertRefused("input.t_unrecognized_flag", "argument -h/--help: ignored explicit argument ''.",
                "FLAG-FIX", "--zz", "--help=");
    }

    @Test
    void aDashANegativeNumberOrATokenWithASpaceIsPositional() {
        assertEquals(List.of("-"), scan("-").positionals());
        assertEquals(List.of("-1"), scan("-1").positionals());
        assertEquals(List.of("-1.5"), scan("-1.5").positionals());
        assertEquals(List.of("--x y"), scan("--x y").positionals());
        assertEquals(List.of("--x=a b"), scan("--x=a b").positionals());
        assertEquals(List.of("plain"), scan("plain").positionals());
    }

    @Test
    void anUnknownFlagIsDeferredAndTheFirstIsRefusedAfterTheScan() {
        assertUnknownFlag("--zz", "--zz", "--yy");
        assertUnknownFlag("--zz", "--zz", "a", "b");
        assertUnknownFlag("--zz=1", "--zz=1");
        assertUnknownFlag("--ou", "--ou", "o"); // no abbreviation
        assertUnknownFlag("-x", "-x");
    }

    @Test
    void anExtraPositionalIsDeferredAndRefusedInArgvOrder() {
        assertRefused("input.t_unrecognized_flag", "unrecognized argument 'b'.", "EXTRA-FIX", "a", "b", "--zz");
        assertRefused("input.t_unrecognized_flag", "unrecognized argument 'b'.", "EXTRA-FIX", "a", "--", "b");
        // a dash-led extra is worded as a flag, as argparse's leftovers are
        assertUnknownFlag("-2", "-1", "-2");
        assertUnknownFlag("--out", "--", "a", "--out", "o");
    }

    @Test
    void theSeparatorMakesEveryLaterTokenPositional() {
        Argv.Result r = scan("--out", "o", "--", "--");
        assertEquals("o", r.value("--out"));
        assertEquals(List.of("--"), r.positionals());
        assertRefused("input.t_unrecognized_flag", "unrecognized argument 'a'.", "EXTRA-FIX", "--", "--", "a");
    }

    @Test
    void assessGrammarDeclaresEveryOptionInItsUsage() {
        Map<String, Argv.Kind> options = Cli.ASSESS_GRAMMAR.options();
        for (String name : options.keySet()) {
            assertTrue(Cli.ASSESS_USAGE.contains("[" + name) || Cli.ASSESS_USAGE.contains(", " + name), name);
        }
        assertEquals(20, options.size());
        assertEquals(Argv.Kind.APPEND, options.get("--catalog-dir"));
        assertEquals(Argv.Kind.FLAG, options.get("--package-for-sharing"));
        assertEquals(Argv.Kind.VALUE, options.get("--report-language"));
        assertFalse(options.containsKey("--manual"));
        assertFalse(options.containsKey("--probes"));
        assertEquals(1, Cli.ASSESS_GRAMMAR.maxPositionals());
        InputError err = assertThrows(InputError.class,
                () -> Argv.scan(new String[] {"--emit"}, Cli.ASSESS_GRAMMAR));
        assertEquals("input.assess_flag_needs_value", err.key);
        assertEquals("pass --emit <formats>.", err.fix);
    }
}
