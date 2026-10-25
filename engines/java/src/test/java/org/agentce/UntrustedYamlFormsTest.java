package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The profile, binding and register read odd YAML as Python's untrusted loaders read it (18.184p): the profile
 * through the PyYAML-equivalent tree, not Jackson, and a SafeLoader-only tag refused in every file, merge sources
 * included. */
class UntrustedYamlFormsTest {

    private static Path write(Path dir, String name, String text) throws IOException {
        return Files.writeString(dir.resolve(name), text);
    }

    @Test
    void formsPyyamlsScannerRefusesAreRefused() {
        for (String text : List.of(
                "a: [a?, b]\n", "a: [a ?, b]\n", "a: {a?: 1}\n", "a: [a ?]\n", "a: [a?b]\n", "a: [lookup ?,\n \"yes\"]\n",
                "a: b\t\n", "a: 'b'\t\n", "a: [b]\t\n", "a\t: b\n", "a:\tb\n", "-\ta\n", "a: |\t\n  x\n",
                "a: [b,\tc]\n", "a: b\t# c\n", "a: {: 1}\n", "a: [: 1]\n", "a: {a: 1, : 2}\n")) {
            assertThrows(IllegalArgumentException.class, () -> Readiness.composePyyaml(text), text);
        }
    }

    @Test
    void aProfileWithAMergeKeyIsOutsideTheYamlProfile(@TempDir Path tmp) throws IOException {
        // Anchors, aliases and << are refused before the YAML is read (SPEC 8.7, 18.184u).
        Path p = write(tmp, "p.yaml", "b: &b {catalogs: [eu-ai-act]}\n<<: *b\n");
        InputError e = assertThrows(InputError.class, () -> Profile.load(p));
        assertEquals("input.profile_invalid", e.key);
        assertTrue(e.reason.endsWith(" is outside the YAML profile: anchor at line 1 (SPEC 8.7)."), e.reason);
    }

    @Test
    void aProfileWithAYamlErrorIsRefusedAsAProfileProblem(@TempDir Path tmp) throws IOException {
        for (String text : List.of("x: !!binary aGVsbG8=\n", "x: !!set {a: null}\n", "<<:\n", "x: [a\n")) {
            InputError e = assertThrows(InputError.class, () -> Profile.load(write(tmp, "p.yaml", text)), text);
            assertEquals("input.profile_invalid", e.key, text);
        }
    }

    @Test
    void anObservationWindowBoundThatIsNotAnRfc3339DateIsRefused(@TempDir Path tmp) throws IOException {
        // 18.87: the window end is what deviation expiries are checked against.
        for (String[] c : List.of(
                new String[] {"observation_window: {start: \"2026-01-01T00:00:00Z\", end: \"foo\"}\n", "end"},
                new String[] {"observation_window: {start: \"2026-01-01T00:00:00Z\", end: 2026-02-30}\n", "end"},
                new String[] {"observation_window: {start: \"2026-01-01T00:00:00Z\", end: null}\n", "end"},
                new String[] {"observation_window: {start: \"foo\", end: \"2026-02-01T00:00:00Z\"}\n", "start"})) {
            InputError e = assertThrows(InputError.class, () -> Profile.load(write(tmp, "p.yaml", c[0])), c[0]);
            assertEquals("input.profile_invalid", e.key, c[0]);
            assertTrue(e.reason.endsWith(
                    " has an observation_window " + c[1] + " that is not an RFC 3339 date or date-time."), e.reason);
        }
    }

    @Test
    void anObservationWindowThatIsNotAMappingIsRefused(@TempDir Path tmp) throws IOException {
        InputError e = assertThrows(
                InputError.class, () -> Profile.load(write(tmp, "p.yaml", "observation_window: \"2026\"\n")));
        assertEquals("input.profile_invalid", e.key);
        assertTrue(e.reason.endsWith(" has an observation_window that is not a mapping of start and end."), e.reason);
    }

    @Test
    void anUnquotedYamlDateBoundIsAcceptedAsItsPythonText(@TempDir Path tmp) throws IOException {
        Profile p = Profile.load(
                write(tmp, "p.yaml", "observation_window: {start: 2026-01-01, end: 2026-02-01T10:00:00Z}\n"));
        assertEquals(Map.of("start", "2026-01-01", "end", "2026-02-01 10:00:00+00:00"), p.observationWindow);
    }

    @Test
    void aSafeLoaderOnlyTagOnAMergeSourceIsRefusedInTheRegister(@TempDir Path tmp) throws IOException {
        Path r = write(tmp, "r.yaml", "deviations:\n  - <<: !!set {control: null}\n");
        InputError e = assertThrows(InputError.class, () -> Readiness.loadDeviationRegister(r));
        assertEquals("input.deviation_invalid", e.key);
    }

    @Test
    void onlyAUnicode15DigitReadsAsADigit() {
        // A tag is outside the YAML profile (SPEC 8.7, 18.184u), so no tagged digit is read at all.
        assertEquals(new YamlProfile.Violation("tag", 1), assertThrows(YamlProfile.ProfileException.class,
                () -> Readiness.composePyyaml("a: !!int \"١٠\"\n")).violation());
        for (String text : List.of("a: !!int \"𐵁𐵀\"\n", "a: !!float \"𐵁.5\"\n", "a: !!int \"𖵱\"\n")) {
            assertThrows(IllegalArgumentException.class, () -> Readiness.composePyyaml(text), text);
        }
    }

    @Test
    void aListControlIsNamedAsPythonPrintsIt() {
        List<String> problems = Readiness.deviationLint(
                List.of(Readiness.toJsonNode(java.util.Map.of("control", List.of("DOC-01")))),
                java.util.Set.of("DOC-01"), java.util.Map.of(), java.util.Set.of(), null, 180);
        assertEquals("['DOC-01']: control is not in the catalog", problems.get(0));
    }

    @Test
    void nestedAliasesUnderAnIgnoredKeyAreRefusedAtTheFirstAnchor(@TempDir Path tmp) throws IOException {
        StringBuilder text = new StringBuilder("x:\n  a0: &a0 [v]\n");
        for (int i = 1; i <= 25; i++) {
            String p = "*a" + (i - 1);
            text.append("  a").append(i).append(": &a").append(i).append(" [")
                    .append(String.join(", ", java.util.Collections.nCopies(9, p))).append("]\n");
        }
        text.append("catalogs: [eu-ai-act]\n");
        InputError e = assertThrows(InputError.class, () -> Profile.load(write(tmp, "p.yaml", text.toString())));
        assertEquals("input.profile_invalid", e.key);
        assertTrue(e.reason.endsWith(" is outside the YAML profile: anchor at line 2 (SPEC 8.7)."), e.reason);
    }

    @Test
    void aThirtyLevelDoublingChainUnderAnIgnoredKeyFinishesQuickly() {
        StringBuilder chain = new StringBuilder("x-chain:\n  a0: &a0 {k: v}\n");
        for (int i = 1; i <= 30; i++) {
            chain.append("  a").append(i).append(": &a").append(i).append(" {l: *a").append(i - 1)
                    .append(", r: *a").append(i - 1).append("}\n");
        }
        for (String rest : List.of("catalogs: [eu-ai-act]\n", "deviations: []\n", "task_scopes: []\n")) {
            assertTimeoutPreemptively(java.time.Duration.ofSeconds(10),
                    () -> assertThrows(YamlProfile.ProfileException.class, () -> Readiness.composePyyaml(chain + rest)),
                    rest);
        }
    }

    @Test
    void aKeyThatDoesNotReadAsTextIsOutsideTheYamlProfile() {
        // PyYAML's key equality no longer decides a repeat: a non-string key is refused first (SPEC 8.7, 18.184u),
        // and a repeat is the same written key text twice (duplicate_key).
        for (String text : List.of("{1: a, 1.0: b}", "{1: a, true: b}", "{0: a, false: b}", "{0x1: a, 1: b}",
                "{null: a, ~: b}", "{.nan: a, .nan: b}", "{2026-01-01: a, 2026-01-01: b}",
                "{2026-01-01T00:00:00Z: a, 2026-01-01T01:00:00+01:00: b}", "{\"1\": a, 1: b}", "{null: a, \"null\": b}",
                "{2026-01-01: a, 2026-01-02: b}", "{2026-01-01: a, \"2026-01-01\": b}", "{1.5: a, 15e-1: b}")) {
            assertEquals("non_string_key", assertThrows(YamlProfile.ProfileException.class,
                    () -> Readiness.composePyyaml(text + "\n"), text).violation().rule(), text);
        }
        assertEquals(new YamlProfile.Violation("duplicate_key", 1), assertThrows(YamlProfile.ProfileException.class,
                () -> Readiness.composePyyaml("{\"a\": 1, a: 2}\n")).violation());
    }
}
