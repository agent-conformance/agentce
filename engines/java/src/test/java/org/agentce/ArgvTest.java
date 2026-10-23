package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** {@link Argv#scan} under the vendored SPEC 8.5 grammar (18.184w), against readiness's and assess's
 * own tables: each answer is the one Python's {@code agentce.argv.scan} gives the same argv. The case
 * table ({@link ArgvCasesTest}) runs every rule through the CLI; these pin the scanner's own reads. */
class ArgvTest {
    private static final Argv.Command READINESS = Argv.command("readiness");
    private static final String KEY = "input.readiness_unrecognized_flag";
    private static final String FLAG_FIX =
            "pass --gaps, --deviations, --catalog-dir, --trust-root or --allow-unverified-catalog, or drop the flag.";
    private static final String EXTRA_FIX = "pass exactly one report directory: `agentce readiness <report-dir>`.";

    private static Argv.Scan scan(String... argv) {
        return Argv.scan(READINESS, List.of(argv));
    }

    private static void assertRefused(String key, String cause, String fix, String... argv) {
        InputError err = assertThrows(InputError.class, () -> scan(argv), String.join(" ", argv));
        assertEquals(List.of(key, cause, fix), List.of(err.key, err.reason, err.fix), String.join(" ", argv));
    }

    private static void assertUnknownFlag(String token, String... argv) {
        assertRefused(KEY, "unrecognized flag '" + token + "'.", FLAG_FIX, argv);
    }

    private static void assertNeedsValue(String flag, String... argv) {
        assertRefused(KEY, "flag '" + flag + "' needs a value.", "pass " + flag + " <path>.", argv);
    }

    @Test
    void theTableIsTheVendoredGrammar() {
        assertEquals(List.of("python", "typescript", "java"), READINESS.implementedIn());
        assertEquals(List.of("python"), Argv.command("collect").implementedIn());
        assertEquals(1, READINESS.level().maxPositionals());
        assertNull(Argv.command("diff").level().maxPositionals());
        assertTrue(READINESS.level().options().get("--catalog-dir").repeatable());
        assertTrue(READINESS.level().options().containsKey("--json")); // the globals join every level
        assertFalse(Argv.isCommand("read"));
    }

    @Test
    void anEmptyArgvReadsNothing() {
        Argv.Scan r = scan();
        assertFalse(r.help);
        assertEquals(Map.of(), r.values);
        assertEquals(List.of(), r.positionals);
        assertEquals(List.of(), r.list("--catalog-dir"));
        assertFalse(r.flag("--json"));
    }

    @Test
    void aValueIsTheNextTokenOrTheWholeEqualsRest() {
        Argv.Scan r = scan("r", "--gaps", "g", "--deviations=a b=c", "--json", "--trust-root=-1", "--catalog-dir=");
        assertEquals(Map.of("--gaps", "g", "--deviations", "a b=c", "--trust-root", "-1"), r.values);
        assertEquals(Map.of("--catalog-dir", List.of("")), r.lists);
        assertEquals(List.of("r"), r.positionals);
        assertTrue(r.flag("--json"));
    }

    @Test
    void aDashLedNextTokenIsNeverAValue() {
        assertNeedsValue("--gaps", "--gaps", "-1");
        assertNeedsValue("--gaps", "--gaps", "-");
        assertNeedsValue("--gaps", "--gaps", "-x y");
        assertNeedsValue("--gaps", "--gaps", "--");
        assertNeedsValue("--gaps", "--gaps");
        assertNeedsValue("--gaps", "--gaps", "--trust-root", "x");
    }

    @Test
    void helpInAValueSlotIsNoHelp() {
        assertNeedsValue("--gaps", "--gaps", "--help");
        assertNeedsValue("--gaps", "--gaps", "-h");
        // a later help still wins
        assertTrue(scan("--gaps", "-h", "--help").help);
    }

    @Test
    void helpWinsOverEveryRefusal() {
        assertTrue(scan("-h").help);
        assertTrue(scan("a", "b", "--help").help);
        assertTrue(scan("--zz", "-h").help);
        assertTrue(scan("--gaps", "a", "--gaps", "b", "-h").help);
        assertTrue(scan("-h", "--json=1").help);
        Argv.Scan r = scan("--", "-h");
        assertFalse(r.help);
        assertEquals(List.of("-h"), r.positionals);
    }

    @Test
    void aSingleValuedOptionGivenTwiceIsRefusedARepeatableOneKeepsOrder() {
        assertRefused(KEY, "flag '--gaps' was given more than once.", "give --gaps once.", "--gaps", "a", "--gaps=a");
        assertRefused(KEY, "flag '--json' was given more than once.", "give --json once.", "--json", "--json");
        assertEquals(List.of("b", "a"), scan("--catalog-dir", "b", "--catalog-dir=a").list("--catalog-dir"));
    }

    @Test
    void aGlobalGivenBeforeTheCommandWordCountsAsGiven() {
        InputError err = assertThrows(InputError.class,
                () -> Argv.scan(READINESS, List.of("r", "--json"), List.of("--json"), null, null));
        assertEquals("flag '--json' was given more than once.", err.reason);
        assertTrue(Argv.scan(READINESS, List.of("r", "--debug"), List.of("--json"), null, null).flag("--debug"));
    }

    @Test
    void aFlagWithAValueIsRefused() {
        assertRefused(KEY, "flag '--allow-unverified-catalog' takes no value.",
                "drop the value: --allow-unverified-catalog.", "--allow-unverified-catalog=yes");
        assertRefused(KEY, "flag '-h' takes no value.", "drop the value: -h.", "-h=x");
        assertRefused(KEY, "flag '--help' takes no value.", "drop the value: --help.", "--help=");
    }

    @Test
    void everyDashTokenThatIsNotASpellingIsAnUnknownFlag() {
        assertUnknownFlag("--GAPS", "--GAPS", "x");
        assertUnknownFlag("--gap", "--gap", "x");
        assertUnknownFlag("-jq", "-jq");
        assertUnknownFlag("-1", "-1");
        assertUnknownFlag("-1.5", "-1.5");
        assertUnknownFlag("-", "-");
        assertUnknownFlag("-x y", "-x y");
        assertUnknownFlag("--gaps＝x", "--gaps＝x");
    }

    @Test
    void theFirstRefusalInArgvOrderWins() {
        assertUnknownFlag("--zz", "--zz", "--gaps");
        assertNeedsValue("--gaps", "--gaps", "--zz");
        assertRefused(KEY, "unrecognized argument 'b'.", EXTRA_FIX, "a", "b", "--zz");
    }

    @Test
    void theSeparatorEndsTheOptions() {
        Argv.Scan r = scan("--gaps", "g", "--", "--json");
        assertEquals("g", r.value("--gaps"));
        assertEquals(List.of("--json"), r.positionals);
        assertFalse(r.flag("--json"));
        assertRefused(KEY, "unrecognized flag '--'.", FLAG_FIX, "r", "--");
        assertRefused(KEY, "unrecognized flag '--'.", FLAG_FIX, "--");
        assertRefused(KEY, "unrecognized argument 'b'.", EXTRA_FIX, "a", "--", "b");
        // a level that takes no positional refuses `--` itself
        InputError err = assertThrows(
                InputError.class, () -> Argv.scan(Argv.command("quickstart"), List.of("--", "x")));
        assertEquals("unrecognized flag '--'.", err.reason);
    }

    @Test
    void assessHasItsOwnNeedsValueKeyAndHints() {
        InputError err = assertThrows(InputError.class, () -> Argv.scan(Argv.command("assess"), List.of("--emit")));
        assertEquals(List.of("input.assess_flag_needs_value", "flag '--emit' needs a value.", "pass --emit <formats>."),
                List.of(err.key, err.reason, err.fix));
        assertEquals("nope", Argv.scan(Argv.command("assess"), List.of("--emit", "nope")).value("--emit"));
        assertEquals(List.of("f"), Argv.scan(Argv.command("assess"), List.of("f", "--json")).positionals);
    }
}
