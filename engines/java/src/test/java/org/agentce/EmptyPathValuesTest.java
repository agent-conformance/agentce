package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * An empty path value ('' or --opt=) is refused with the role's key before anything is read or written, never read
 * as the working directory, the default, or no value (item 18.178). Mirrors Python's
 * tests/test_empty_path_values.py for the commands this engine implements. The JVM cannot change its working
 * directory, so the check that nothing is written looks at the real one ({@code Paths.get("")}).
 */
class EmptyPathValuesTest {
    private static final Path REPO = TestPaths.repoRoot();
    private static final Path QUICKSTART = REPO.resolve("corpus/quickstart");
    private static final String BUNDLE = QUICKSTART.resolve("evidence").toString();
    private static final String PROFILE = QUICKSTART.resolve("applicability.yaml").toString();
    // A catalog the vendored trust root verifies, so a dropped --trust-root '' would assess and exit 0 or 2.
    private static final String[] ASSESS_SIGNED = {
        "assess", "--bundle", BUNDLE, "--profile", PROFILE,
        "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
        "--catalog-dir", REPO.resolve("spec/catalogs/base/eu-ai-act").toString(),
    };

    private static List<String> workingFolder() throws IOException {
        try (Stream<Path> entries = Files.list(Paths.get("").toAbsolutePath())) {
            return entries.map(Path::toString).sorted().toList();
        }
    }

    private static String[] with(String[] head, String... tail) {
        return Stream.concat(Stream.of(head), Stream.of(tail)).toArray(String[]::new);
    }

    private static void assertRefused(String key, String... argv) throws IOException {
        List<String> before = workingFolder();
        JsonNode env = Fixtures.runJson(argv);
        String shown = String.join(" ", argv);
        assertEquals(3, env.get("exit_code").asInt(), shown + " -> " + env);
        assertEquals(key, env.get("error").get("message_key").asText(), shown + " -> " + env);
        assertEquals(before, workingFolder(), shown);
    }

    @Test
    void anEmptyPathValueIsRefusedWithTheRoleKeyAndWritesNothing(@TempDir Path dir) throws IOException {
        String r = Files.createDirectory(dir.resolve("r")).toString();
        String a = Files.writeString(dir.resolve("a.json"), "[]").toString();
        assertRefused("input.records_not_a_directory", "assess", "");
        assertRefused("input.bundle_not_a_directory", "assess", "--bundle", "", "--profile", PROFILE);
        assertRefused("input.bundle_not_a_directory", "assess", "--bundle=", "--profile", PROFILE);
        assertRefused("input.catalog_dir_not_a_directory",
                "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--catalog-dir", "");
        assertRefused("input.out_dir_unwritable", "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--out", "");
        assertRefused("input.out_dir_unwritable", "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--out=");
        assertRefused("input.state_dir_unwritable", "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--state", "");
        assertRefused("input.state_dir_unwritable", "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--state=");
        assertRefused("input.trust_root_not_a_file", with(ASSESS_SIGNED, "--trust-root", ""));
        assertRefused("input.trust_root_not_a_file", with(ASSESS_SIGNED, "--trust-root="));
        assertRefused("input.out_dir_unwritable", "quickstart", "--out", "");
        assertRefused("input.report_dir_not_a_directory", "sign", "");
        assertRefused("input.report_dir_not_a_directory", "readiness", "");
        assertRefused("input.gaps_not_a_file", "readiness", r, "--gaps", "");
        assertRefused("input.deviations_not_a_file", "readiness", r, "--deviations=");
        assertRefused("input.catalog_dir_not_a_directory", "readiness", r, "--catalog-dir", "");
        assertRefused("input.out_file_unwritable", "report", "--from", a, "--out", "");
    }

    @Test
    void kmsSignWithAnEmptyKeyGetsTheAbsentKeyFileKey(@TempDir Path dir) throws IOException {
        String rep = dir.resolve("rep").toString();
        assertEquals(0, Fixtures.runJson("quickstart", "--out", rep).get("exit_code").asInt());
        assertRefused("input.key_not_a_file", "sign", rep, "--as", "claimant", "--profile", "kms", "--key", "");
        assertRefused("input.key_not_a_file", "sign", rep, "--as", "claimant", "--profile", "kms", "--key=");
    }

    @Test
    void theSignedCatalogAssessesWithNoTrustRootSoTheRefusalIsTheEmptyValues(@TempDir Path dir) {
        int exit = Fixtures.runJson(with(ASSESS_SIGNED, "--out", dir.resolve("out").toString()))
                .get("exit_code").asInt();
        assertTrue(exit == 0 || exit == 2, "exit " + exit);
    }
}
