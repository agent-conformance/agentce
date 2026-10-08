package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The action level of {@link Argv#scan} (argparse's subparsers, 18.111), each answer checked against
 * what Python's argparse gives the same argv, and the adapters run {@code conformance run --adapters}
 * folds into the implementation report. */
class ArgvActionTest {
    private static Map<String, Argv.Kind> options(String... values) {
        Map<String, Argv.Kind> options = new LinkedHashMap<>();
        options.put("-h", Argv.Kind.HELP);
        options.put("--help", Argv.Kind.HELP);
        options.put("--json", Argv.Kind.FLAG);
        for (String value : values) {
            options.put(value, Argv.Kind.VALUE);
        }
        return options;
    }

    private static final Argv.Grammar RUN = new Argv.Grammar(
            options("--engine", "--out"), 0, "input.run_flag", "input.run_flag", "RUN-FIX", "RUN-EXTRA", Map.of(),
            Map.of("--engine", "input.engine_missing"), Map.of(), false);

    private static final Argv.Grammar CMD = new Argv.Grammar(
            options(), 0, "input.cmd_flag", "input.cmd_flag", "CMD-FIX", "CMD-EXTRA", Map.of(), Map.of(), Map.of(),
            false,
            new Argv.Action(Map.of("run", RUN),
                    () -> new InputError("input.cmd_action", "the only action is `run`.", "ACTION-FIX")));

    private static Argv.Result scan(String... argv) {
        return Argv.scan(argv, CMD);
    }

    private static void assertRefused(String key, String cause, String... argv) {
        InputError err = assertThrows(InputError.class, () -> scan(argv), String.join(" ", argv));
        assertEquals(key, err.key, String.join(" ", argv));
        assertEquals(cause, err.reason, String.join(" ", argv));
    }

    @Test
    void theNamedActionsRestIsScannedByItsGrammar() {
        Argv.Result r = scan("--json", "run", "--engine=e", "--out", "o", "--engine", "f");
        assertFalse(r.help());
        assertEquals("run", r.action());
        assertEquals("f", r.value("--engine")); // last wins
        assertEquals("o", r.value("--out"));
        assertTrue(r.flag("--json")); // the command level's flag is kept
        assertNull(scan().action());
        assertNull(scan("--json").action());
    }

    @Test
    void anUnknownActionIsRefusedAtOnceBeforeAnEarlierUnknownFlag() {
        assertRefused("input.cmd_action", "the only action is `run`.", "nope");
        assertRefused("input.cmd_action", "the only action is `run`.", "--he", "nope");
        // an unknown flag takes no value, so the next token is the action
        assertRefused("input.cmd_action", "the only action is `run`.", "--engine", "x", "run");
        // `--` stands where the action goes, and is no action
        assertRefused("input.cmd_action", "the only action is `run`.", "--", "run");
        assertRefused("input.cmd_action", "the only action is `run`.", "-", "run");
    }

    @Test
    void helpBeforeTheActionNamesTheCommandsUsageAndAfterItTheActions() {
        Argv.Result before = scan("-h", "run");
        assertTrue(before.help());
        assertNull(before.action());
        assertNull(scan("--help").action());
        Argv.Result after = scan("run", "--no-such", "--help");
        assertTrue(after.help());
        assertEquals("run", after.action());
        // HELP wins over an unknown flag at the command level too
        Argv.Result both = scan("--he", "run", "-h");
        assertTrue(both.help());
        assertEquals("run", both.action());
        // a value on HELP is refused at once
        assertRefused("input.cmd_flag", "argument -h/--help: ignored explicit argument 'x'.", "--help=x", "run");
    }

    @Test
    void theCommandLevelsUnknownFlagIsRefusedBeforeTheActions() {
        assertRefused("input.cmd_flag", "unrecognized flag '--he'.", "--he", "run", "--no-such");
        assertRefused("input.run_flag", "unrecognized flag '--no-such'.", "run", "--no-such");
        assertRefused("input.run_flag", "unrecognized argument 'run'.", "run", "run");
        assertRefused("input.run_flag", "unrecognized flag '--'.", "run", "--engine", "x", "--", "y");
        // a missing value in the action is refused at once, before the command level's unknown flag
        assertRefused("input.engine_missing", "flag '--engine' needs a value.", "--he", "run", "--engine");
    }

    @Test
    void theClusterPreScanStopsAtTheActionAndTheActionHasItsOwn() {
        assertRefused("input.cmd_flag", "unrecognized flag '-hx'.", "-hx", "run");
        assertRefused("input.cmd_flag", "unrecognized flag '-hx'.", "--he", "-hx", "nope");
        // past the action the command's pre-scan stops, so the action's names the cluster with its key
        assertRefused("input.run_flag", "unrecognized flag '-hx'.", "run", "-hx");
        // and an unknown action is still refused first when the cluster comes after it
        assertRefused("input.cmd_action", "the only action is `run`.", "nope", "-hx");
    }

    @Test
    void aGrammarWithNoActionScansAsBefore() {
        Argv.Grammar plain = new Argv.Grammar(
                options("--out"), 1, "input.p_flag", "input.p_flag", "P-FIX", "P-EXTRA", Map.of());
        Argv.Result r = Argv.scan(new String[] {"a", "--out", "o"}, plain);
        assertNull(r.action());
        assertEquals(List.of("a"), r.positionals());
        assertEquals("o", r.value("--out"));
        // `--` makes the rest positional, a second positional is an extra, -h returns with no action
        assertEquals(List.of("-h"), Argv.scan(new String[] {"--", "-h"}, plain).positionals());
        InputError extra = assertThrows(InputError.class, () -> Argv.scan(new String[] {"a", "b"}, plain));
        assertEquals("unrecognized argument 'b'.", extra.reason);
        // the cluster pre-scan does not stop at a positional
        InputError cluster = assertThrows(InputError.class, () -> Argv.scan(new String[] {"a", "-hx"}, plain));
        assertEquals("unrecognized flag '-hx'.", cluster.reason);
        Argv.Result help = Argv.scan(new String[] {"a", "--no-such", "-h"}, plain);
        assertTrue(help.help());
        assertNull(help.action());
        // assess's own grammar still reads its folder
        assertEquals(List.of("f"), Argv.scan(new String[] {"f", "--json"}, Cli.ASSESS_GRAMMAR).positionals());
    }

    // --- conformance run --adapters: the adapters' own orchestrator, folded into the report ---

    @Test
    void theOrchestratorsJsonIsFoldedInWithClaimFull(@TempDir Path dir) {
        List<List<String>> calls = new ArrayList<>();
        Conformance.Orchestrator fake = (adaptersDir, args) -> {
            calls.add(args);
            return new String[] {
                "{\"adapters\": [{\"name\": \"sample\"}], \"total\": 1, \"identical\": 1, \"round_trip\": true}\n", ""};
        };
        JsonNode detail = Conformance.adapterConformance(dir, Path.of("out"), fake);
        assertEquals(1, detail.get("total").asInt());
        assertEquals("sample", detail.get("adapters").get(0).get("name").asText());
        assertEquals("full", Conformance.adapterClaim(detail));
        // --json, then --out resolved against the working directory, never the adapters directory
        assertEquals(List.of("--json", "--out", Conformance.pyResolve(Path.of("out")).toString()), calls.get(0));
        assertTrue(Path.of(calls.get(0).get(2)).isAbsolute());
        Conformance.adapterConformance(dir, null, fake);
        assertEquals(List.of("--json"), calls.get(1));
    }

    @Test
    void stdoutThatIsNotJsonGivesTheErrorRecordAndClaimNone(@TempDir Path dir) {
        JsonNode detail = Conformance.adapterConformance(
                dir, null, (adaptersDir, args) -> new String[] {"not json\n", "  no adapters found\n"});
        assertEquals(
                "{\"adapters\":[],\"total\":0,\"identical\":0,\"round_trip\":false,\"error\":\"no adapters found\"}",
                Json.compact(detail));
        assertEquals("none", Conformance.adapterClaim(detail));
        // with stderr empty the error is stdout's, cut to its last 800 characters
        String longOut = "x".repeat(900) + "y";
        JsonNode fromStdout = Conformance.adapterConformance(
                dir, null, (adaptersDir, args) -> new String[] {longOut, ""});
        assertEquals(longOut.substring(longOut.length() - 800), fromStdout.get("error").asText());
    }

    @Test
    void theAdaptersClaimFollowsPythons() {
        assertEquals("partial", Conformance.adapterClaim(
                Json.parse("{\"total\": 1, \"identical\": 1, \"round_trip\": false}")));
        assertEquals("partial", Conformance.adapterClaim(Json.parse("{\"total\": 2, \"identical\": 1, \"round_trip\": true}")));
        assertEquals("none", Conformance.adapterClaim(Json.parse("{\"total\": 0, \"identical\": 0, \"round_trip\": true}")));
        assertEquals("none", Conformance.adapterClaim(Json.parse("{}")));
    }
}
