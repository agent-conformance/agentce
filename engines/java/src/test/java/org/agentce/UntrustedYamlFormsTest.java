package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
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
    void theScannerRefusesWhatPyyamlsScannerRefuses() {
        for (String text : List.of(
                "a: [a?, b]\n", "a: [a ?, b]\n", "a: {a?: 1}\n", "a: [a ?]\n", "a: [a?b]\n", "a: [lookup ?,\n \"yes\"]\n",
                "a: b\t\n", "a: 'b'\t\n", "a: [b]\t\n", "a\t: b\n", "a:\tb\n", "-\ta\n", "a: |\t\n  x\n",
                "a: [b,\tc]\n", "a: b\t# c\n", "a: {: 1}\n", "a: [: 1]\n", "a: {a: 1, : 2}\n")) {
            assertThrows(IllegalArgumentException.class, () -> Readiness.composePyyaml(text, false), text);
        }
        for (String text : List.of(
                "a: a?\n", "a: [\"a?\", b]\n", "a: [? a]\n", "a: \"b\tc\"\n", "a: b # c\td\n", "a: |\n  x\ty\n",
                "a: [b, c]\n", "a: {b: c,\n  d: e}\n", "? [a, b]\n: c\n", "a: &x {k: v}\nb: *x\n")) {
            assertEquals(null, PyyamlScanner.problem(text), text);
        }
    }

    @Test
    void aProfileReadsItsCatalogsThroughAMergeKey(@TempDir Path tmp) throws IOException {
        Path p = write(tmp, "p.yaml", "b: &b {catalogs: [eu-ai-act]}\n<<: *b\n");
        assertEquals(List.of("eu-ai-act"), Profile.load(p).catalogs);
    }

    @Test
    void aProfileWithAYamlErrorIsRefusedAsAProfileProblem(@TempDir Path tmp) throws IOException {
        for (String text : List.of("x: !!binary aGVsbG8=\n", "x: !!set {a: null}\n", "<<:\n", "x: [a\n")) {
            InputError e = assertThrows(InputError.class, () -> Profile.load(write(tmp, "p.yaml", text)), text);
            assertEquals("input.profile_invalid", e.key, text);
        }
    }

    @Test
    void aSafeLoaderOnlyTagOnAMergeSourceIsRefusedInTheRegister(@TempDir Path tmp) throws IOException {
        Path r = write(tmp, "r.yaml", "deviations:\n  - <<: !!set {control: null}\n");
        InputError e = assertThrows(InputError.class, () -> Readiness.loadDeviationRegister(r));
        assertEquals("input.deviation_invalid", e.key);
    }

    @Test
    void onlyAUnicode15DigitReadsAsADigit() {
        assertEquals(java.math.BigInteger.TEN, ((java.util.Map<?, ?>) Readiness.composePyyaml("a: !!int \"١٠\"\n", false)).get("a"));
        for (String text : List.of("a: !!int \"𐵁𐵀\"\n", "a: !!float \"𐵁.5\"\n", "a: !!int \"𖵱\"\n")) {
            assertThrows(IllegalArgumentException.class, () -> Readiness.composePyyaml(text, false), text);
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
    void nestedAliasesUnderAnIgnoredKeyAreReadInOnePass(@TempDir Path tmp) throws IOException {
        StringBuilder text = new StringBuilder("x:\n  a0: &a0 [v]\n");
        for (int i = 1; i <= 25; i++) {
            String p = "*a" + (i - 1);
            text.append("  a").append(i).append(": &a").append(i).append(" [")
                    .append(String.join(", ", java.util.Collections.nCopies(9, p))).append("]\n");
        }
        text.append("catalogs: [eu-ai-act]\n");
        assertEquals(List.of("eu-ai-act"), Profile.load(write(tmp, "p.yaml", text.toString())).catalogs);
    }

    @Test
    void aThirtyLevelDoublingChainUnderAnIgnoredKeyFinishesQuickly() {
        StringBuilder chain = new StringBuilder("x-chain:\n  a0: &a0 {k: v}\n");
        for (int i = 1; i <= 30; i++) {
            chain.append("  a").append(i).append(": &a").append(i).append(" {l: *a").append(i - 1)
                    .append(", r: *a").append(i - 1).append("}\n");
        }
        for (String rest : List.of("catalogs: [eu-ai-act]\n", "deviations: []\n", "task_scopes: []\n")) {
            for (boolean uniqueKeys : List.of(false, true)) {
                assertTimeoutPreemptively(java.time.Duration.ofSeconds(10),
                        () -> Readiness.composePyyaml(chain + rest, uniqueKeys), rest);
            }
        }
    }

    @Test
    void aRepeatedKeyIsDecidedByPythonsKeyEquality() {
        for (String text : List.of("{1: a, 1.0: b}", "{1: a, true: b}", "{0: a, false: b}", "{0x1: a, 1: b}",
                "{null: a, ~: b}", "{.nan: a, .nan: b}", "{2026-01-01: a, 2026-01-01: b}",
                "{2026-01-01T00:00:00Z: a, 2026-01-01T01:00:00+01:00: b}")) {
            assertThrows(IllegalArgumentException.class, () -> Readiness.composePyyaml(text + "\n", true), text);
        }
        for (String text : List.of("{\"1\": a, 1: b}", "{null: a, \"null\": b}", "{2026-01-01: a, 2026-01-02: b}",
                "{2026-01-01: a, \"2026-01-01\": b}", "{1.5: a, 15e-1: b}")) {
            Readiness.composePyyaml(text + "\n", true);
        }
    }
}
