package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/** report's command line (18.110): the grammar additions {@link Argv.Choices} and the per-option
 * needs-value key, read through {@link Cli#REPORT_GRAMMAR}, each answer the one Python's report parser
 * gives the same argv; and {@link Report#renderPublicStatement} against Python's {@code
 * render_public_statement} over the same assertions (expected strings captured from Python once). */
class ReportCliTest {
    private static Argv.Result scan(String... argv) {
        return Argv.scan(argv, Cli.REPORT_GRAMMAR);
    }

    private static void assertRefused(String key, String cause, String fix, String... argv) {
        InputError err = assertThrows(InputError.class, () -> scan(argv), String.join(" ", argv));
        assertEquals(key, err.key, String.join(" ", argv));
        assertEquals(cause, err.reason, String.join(" ", argv));
        assertEquals(fix, err.fix, String.join(" ", argv));
    }

    private static final String FORMAT_FIX = "choose one of: md, html, oscal, sarif, public, pack.";
    private static final String ROLE_FIX = "choose one of: provider, deployer.";
    private static final String FLAG_FIX = "run `agentce report --help` for the flags report takes, or drop the flag.";

    @Test
    void aChoiceOutsideItsValuesIsRefusedAtOnceWithItsOwnError() {
        assertRefused("input.report_format", "unknown report format 'nope'.", FORMAT_FIX, "--format", "nope");
        assertRefused("input.report_format", "unknown report format ''.", FORMAT_FIX, "--format=");
        assertRefused("input.report_role", "unknown evidence-pack role 'nope'.", ROLE_FIX, "--role=nope");
        assertRefused("input.report_role", "unknown evidence-pack role ''.", ROLE_FIX, "--role", "");
        // Read in argv order: before a later unknown flag, a later missing value, and a later -h.
        assertRefused("input.report_format", "unknown report format 'nope'.", FORMAT_FIX,
                "--format", "nope", "--no-such-flag");
        assertRefused("input.report_role", "unknown evidence-pack role 'x'.", ROLE_FIX, "--role", "x", "--from");
        assertRefused("input.report_format", "unknown report format 'nope'.", FORMAT_FIX, "--format", "nope", "-h");
        // A choice given after help is never read.
        assertTrue(scan("-h", "--format", "nope").help());
    }

    @Test
    void aChoiceInsideItsValuesIsReadAndTheLastOneWins() {
        Argv.Result r = scan("--format", "md", "--format=public", "--role", "deployer");
        assertEquals("public", r.value("--format"));
        assertEquals("deployer", r.value("--role"));
        assertRefused("input.report_role", "unknown evidence-pack role 'nope'.", ROLE_FIX,
                "--role", "provider", "--role", "nope");
    }

    @Test
    void anOptionWithItsOwnNeedsValueKeyUsesIt() {
        assertRefused("input.from_missing", "flag '--from' needs a value.", "pass --from <value>.", "--from");
        assertRefused("input.validate_missing", "flag '--validate' needs a value.", "pass --validate <value>.",
                "--validate", "--json");
    }

    @Test
    void everyOtherValueOptionUsesTheGrammarsDefaultKey() {
        for (String flag : List.of("--format", "--role", "--catalog", "--language", "--out")) {
            assertRefused("input.report_unrecognized_flag", "flag '" + flag + "' needs a value.",
                    "pass " + flag + " <value>.", flag);
        }
        assertRefused("input.report_unrecognized_flag", "unrecognized flag '--form'.", FLAG_FIX, "--form", "md");
        // With no positional to take it, `--` is itself left over, as argparse leaves it.
        assertRefused("input.report_unrecognized_flag", "unrecognized flag '--'.", FLAG_FIX, "--from", "a", "--", "-h");
        assertRefused("input.report_unrecognized_flag", "unrecognized argument 'extra'.",
                "pass the input with its flag: `agentce report --from <file>` or `--validate <dir>`.", "extra");
    }

    @Test
    void assessKeepsItsGrammarWithNoChoicesAndOneNeedsValueKey() {
        InputError err = assertThrows(InputError.class, () -> Argv.scan(new String[] {"--emit"}, Cli.ASSESS_GRAMMAR));
        assertEquals("input.assess_flag_needs_value", err.key);
        assertEquals("argument --emit: expected one argument", err.reason);
        assertEquals("nope", Argv.scan(new String[] {"--emit", "nope"}, Cli.ASSESS_GRAMMAR).value("--emit"));
    }

    private static final String W = "\"window\":{\"start\":\"2026-05-01T00:00:00Z\",\"end\":\"2026-08-29T00:00:00Z\"},";
    private static final String ODD = "agent_[x]`<y>*#";

    private static String row(String control, String subject, String outcome, String deviation) {
        return "{\"control\":\"" + control + "\",\"control_version\":\"2026.09\",\"subject\":" + Json.quote(subject)
                + ",\"outcome\":\"" + outcome + "\",\"rung\":2,\"mode\":\"automated\"," + W
                + "\"population\":{\"applicable\":1,\"failed\":0},\"severity\":\"high\",\"family\":\""
                + control.split("-")[0] + "\"" + (deviation == null ? "" : ",\"deviation\":\"" + deviation + "\"") + "}";
    }

    private static List<Assertions.Assertion> assertions() {
        String json = "[" + String.join(",",
                row("DAT-01", "spiffe://corp/agents/a", "conformant", null),
                row("DAT-02", "spiffe://corp/agents/a", "partial", "DAT-02"),
                row("AUV-02", ODD, "partial", "AUV-02"),
                row("CND-01", ODD, "insufficient_evidence", null),
                row("AUV-03", "spiffe://corp/agents/a", "non-conformant", null)) + "]";
        List<Assertions.Assertion> out = new ArrayList<>();
        for (JsonNode node : Json.parse(json)) {
            out.add(Assertions.fromJson(node));
        }
        return out;
    }

    private static final String TAIL = """

## Affected persons
Affected persons may obtain an explanation of a decision and raise concerns through the deployer's published contact channel (EU AI Act Arts. 26(11), 85, 86).

## Basis
This statement reports conformance to the named catalog as evaluated by the Agent Conformance Engine over the named evidence and observation window. It is not a legal compliance determination.
""";

    private static final String TABLE_HEAD = """
| Family | conformant | non-conformant | partial | not_applicable | not_assessed | insufficient_evidence |
|---|---|---|---|---|---|---|
""";

    @Test
    void thePublicStatementMatchesPythonsWithDeviationsConductCatalogsAndASanitisedSubject() {
        List<Assertions.Assertion> all = assertions();
        assertEquals(List.of("AUV-02", "DAT-02"), Report.appliedDeviationIds(all));
        String expected = "# Public conformance statement\n\n## Scope\n"
                + "Subjects: `agent_［x］'‹y›*#`, `spiffe://corp/agents/a`\n"
                + "Catalogs: eu-ai-act, nist_ai［rmf］\n"
                + "Date: (unspecified)\n\n## Outcomes by family\n\n" + TABLE_HEAD
                + "| AUV | 0 | 1 | 1 | 0 | 0 | 0 |\n"
                + "| CND | 0 | 0 | 0 | 0 | 0 | 1 |\n"
                + "| DAT | 1 | 0 | 1 | 0 | 0 | 0 |\n"
                + "\n## Accepted deviations\n`AUV-02`, `DAT-02`\n"
                + "\n## Conduct\nOver the observation window, the named subjects acted within their declared "
                + "boundaries and on authorised instructions as evidenced by the Conduct overlay controls listed.\n"
                + TAIL;
        assertEquals(expected, Report.renderPublicStatement(
                all, List.of("eu-ai-act", "nist_ai[rmf]"), Report.appliedDeviationIds(all)));
    }

    @Test
    void thePublicStatementMatchesPythonsWithNoCatalogsNoDeviationsAndNoConduct() {
        List<Assertions.Assertion> one = assertions().subList(0, 1);
        String expected = "# Public conformance statement\n\n## Scope\n"
                + "Subjects: `spiffe://corp/agents/a`\n"
                + "Catalogs: (unspecified)\n"
                + "Date: (unspecified)\n\n## Outcomes by family\n\n" + TABLE_HEAD
                + "| DAT | 1 | 0 | 0 | 0 | 0 | 0 |\n"
                + "\n## Accepted deviations\nNone.\n"
                + TAIL;
        assertEquals(expected, Report.renderPublicStatement(one, null, Report.appliedDeviationIds(one)));
        assertEquals(List.of(), Report.appliedDeviationIds(one));
    }

    @Test
    void theGrammarTakesNoPositionalAndItsUsageIsPythons() {
        assertEquals(0, Cli.REPORT_GRAMMAR.maxPositionals());
        assertTrue(Cli.REPORT_USAGE.startsWith(
                "usage: agentce report [-h] [--json] [--debug] [--quiet] [--from FILE]\n"));
        assertTrue(scan("--from", "a.json", "--help").help());
    }
}
