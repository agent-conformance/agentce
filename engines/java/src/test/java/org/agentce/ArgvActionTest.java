package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The action level of {@link Argv#scan} (SPEC 8.5, conformance's {@code run}), each answer the one
 * Python's {@code agentce.argv.scan} gives the same argv, and the adapters run {@code conformance run --adapters}
 * folds into the implementation report. */
class ArgvActionTest {
    private static final String KEY = "input.conformance_unrecognized_flag";
    private static final String FLAG_FIX =
            "run `agentce conformance run --help` for the flags conformance run takes, or drop the flag.";
    private static final String ACTION = "the only conformance action is `run`.";

    private static Argv.Scan scan(String... argv) {
        return Argv.scan(Argv.command("conformance"), List.of(argv), List.of(), null, Cli::conformanceActionError);
    }

    private static void assertRefused(String key, String cause, String... argv) {
        InputError err = assertThrows(InputError.class, () -> scan(argv), String.join(" ", argv));
        assertEquals(key, err.key, String.join(" ", argv));
        assertEquals(cause, err.reason, String.join(" ", argv));
    }

    @Test
    void theNamedActionsRestIsScannedByItsLevel() {
        Argv.Scan r = scan("--json", "run", "--engine", "e", "--out=o");
        assertFalse(r.help);
        assertEquals("run", r.action);
        assertEquals(Map.of("--engine", "e", "--out", "o"), r.values);
        assertTrue(r.flag("--json")); // the command level's flag is kept
        assertNull(scan().action);
        assertNull(scan("--json").action);
    }

    @Test
    void anOptionIsGivenOnceAcrossBothLevels() {
        assertRefused(KEY, "flag '--engine' was given more than once.", "run", "--engine", "e", "--engine", "f");
        assertRefused(KEY, "flag '--json' was given more than once.", "--json", "run", "--json");
    }

    @Test
    void anUnknownActionIsRefusedInArgvOrder() {
        assertRefused("input.conformance_action", ACTION, "nope");
        assertRefused("input.conformance_action", ACTION, "--", "run");
        // an earlier refusal comes first
        assertRefused(KEY, "unrecognized flag '--he'.", "--he", "nope");
        assertRefused(KEY, "unrecognized flag '-'.", "-", "run");
        assertRefused(KEY, "unrecognized flag '-hx'.", "-hx", "run");
        // the command level has no --engine: it is unknown there
        assertRefused(KEY, "unrecognized flag '--engine'.", "--engine", "x", "run");
    }

    @Test
    void helpNamesTheLevelItWasFirstGivenAt() {
        Argv.Scan before = scan("-h", "run");
        assertTrue(before.help);
        assertNull(before.action);
        Argv.Scan after = scan("--he", "run", "-h");
        assertTrue(after.help);
        assertEquals("run", after.action);
        assertNull(scan("--help", "run", "--help").action);
        assertRefused(KEY, "flag '--help' takes no value.", "--help=x", "run");
    }

    @Test
    void theActionLevelRefusesItsOwnTokens() {
        assertRefused(KEY, "unrecognized flag '--no-such'.", "run", "--no-such");
        assertRefused(KEY, "unrecognized argument 'run'.", "run", "run");
        assertRefused(KEY, "unrecognized flag '--'.", "run", "--engine", "x", "--", "y");
        assertRefused(KEY, "unrecognized flag '-hx'.", "run", "-hx");
        // --engine and --corpus keep their own needs-value keys
        assertRefused("input.engine_missing", "flag '--engine' needs a value.", "run", "--engine");
        assertRefused("input.corpus_missing", "flag '--corpus' needs a value.", "run", "--corpus", "--out", "o");
        assertRefused(KEY, "flag '--out' needs a value.", "run", "--out");
        assertEquals(FLAG_FIX, assertThrows(InputError.class, () -> scan("--zz")).fix);
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

    /** Python's int() reads each count: exact past 2**63, a float truncated, a bool as 0 or 1, a string by
     * int(str)'s grammar; "1.0", "1e0", null and an infinite float are refused. */
    @Test
    void theAdaptersCountsAreReadAsPythonsIntReadsThem() {
        assertEquals("partial", Conformance.adapterClaim(Json.parse(
                "{\"total\": 18446744073709551617, \"identical\": 18446744073709551616, \"round_trip\": true}")));
        assertEquals("partial", Conformance.adapterClaim(Json.parse(
                "{\"total\": 18446744073709551617, \"identical\": 1, \"round_trip\": true}")));
        assertEquals("full", Conformance.adapterClaim(Json.parse("{\"total\": 1.9, \"identical\": 1, \"round_trip\": true}")));
        assertEquals("full", Conformance.adapterClaim(Json.parse("{\"total\": \" 1_0 \", \"identical\": 10, \"round_trip\": true}")));
        assertEquals("full", Conformance.adapterClaim(Json.parse("{\"total\": true, \"identical\": true, \"round_trip\": 1}")));
        assertEquals("partial", Conformance.adapterClaim(Json.parse("{\"total\": 1, \"identical\": 1, \"round_trip\": 0.0}")));
        for (String bad : List.of("\"1.0\"", "\"1e0\"", "null", "1e400", "[]")) {
            assertThrows(IllegalStateException.class, () -> Conformance.adapterClaim(
                    Json.parse("{\"total\": " + bad + ", \"identical\": 1, \"round_trip\": true}")), bad);
        }
    }
}
