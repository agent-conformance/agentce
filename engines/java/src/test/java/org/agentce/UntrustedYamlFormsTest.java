package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

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
}
