package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * A run leaves --out as it found it when it is refused for a bad state.json or for a link in --out that leads
 * outside it (SPEC 8.7, ruling R7; census odd-inputs lines 85, 87 and 89, 18.184s). Mirrors Python's
 * tests/test_out_links_and_early_state.py.
 */
class OutLinksAndEarlyStateTest {
    private static final Path QUICKSTART = TestPaths.repoRoot().resolve("corpus/quickstart");

    private static String[] assess(Path out, String... extra) {
        List<String> argv = new ArrayList<>(List.of(
                "assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--out", out.toString()));
        argv.addAll(List.of(extra));
        return argv.toArray(String[]::new);
    }

    private static List<String> files(Path folder) throws IOException {
        try (Stream<Path> walk = Files.walk(folder)) {
            return walk.filter(p -> !p.equals(folder))
                    .map(p -> folder.relativize(p).toString().replace('\\', '/'))
                    .sorted()
                    .toList();
        }
    }

    private static void assertRefused(JsonNode env, String key) {
        assertEquals(3, env.get("exit_code").asInt(), env.toString());
        assertEquals(key, env.get("error").get("message_key").asText(), env.toString());
    }

    static Stream<Arguments> badStates() {
        return Stream.of(
                Arguments.of("nonutf8", new byte[] {'{', '"', 'x', '"', ':', ' ', '"', (byte) 0xff, (byte) 0xfe, '"', '}'},
                        "input.state_unreadable"),
                Arguments.of("malformed", "{not json".getBytes(StandardCharsets.UTF_8), "input.state_unreadable"),
                Arguments.of("empty", new byte[0], "input.state_unreadable"),
                Arguments.of("array", "[]".getBytes(StandardCharsets.UTF_8), "input.state_unreadable"),
                Arguments.of("duplicate_key",
                        "{\"state_version\": 1, \"state_version\": 1}".getBytes(StandardCharsets.UTF_8),
                        "input.state_unreadable"),
                Arguments.of("no_version", "{}".getBytes(StandardCharsets.UTF_8),
                        "input.state_version_incompatible"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("badStates")
    void aBadStateJsonIsRefusedBeforeOutIsWritten(String id, byte[] content, String key, @TempDir Path dir)
            throws IOException {
        Path state = Files.createDirectory(dir.resolve("st"));
        Files.write(state.resolve("state.json"), content);
        Path out = dir.resolve("out");
        assertRefused(Fixtures.runJson(assess(out, "--state", state.toString())), key);
        assertTrue(!Files.exists(out) || files(out).isEmpty(), () -> out + " was written");
    }

    @Test
    void aStateJsonWithABomIsRead(@TempDir Path dir) throws IOException {
        Path first = dir.resolve("st");
        assertEquals(0, Fixtures.runJson(assess(dir.resolve("o1"), "--state", first.toString()))
                .get("exit_code").asInt());
        Path stateFile = first.resolve("state.json");
        byte[] body = Files.readAllBytes(stateFile);
        byte[] withBom = new byte[body.length + 3];
        withBom[0] = (byte) 0xef;
        withBom[1] = (byte) 0xbb;
        withBom[2] = (byte) 0xbf;
        System.arraycopy(body, 0, withBom, 3, body.length);
        Files.write(stateFile, withBom);
        JsonNode env = Fixtures.runJson(assess(dir.resolve("o2"), "--state", first.toString()));
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
    }

    @Test
    void theStateKeyWinsOverAMissingDeviationsFile(@TempDir Path dir) throws IOException {
        Path state = Files.createDirectory(dir.resolve("st"));
        Files.writeString(state.resolve("state.json"), "{not json");
        JsonNode env = Fixtures.runJson(assess(dir.resolve("out"),
                "--state", state.toString(), "--deviations", dir.resolve("absent.yaml").toString()));
        assertRefused(env, "input.state_unreadable");
    }

    static Stream<Arguments> linkedEntries() {
        List<Arguments> cases = new ArrayList<>();
        Object[][] entries = {
            {"integrity.jsonl", false}, {"manifest.json", false}, {"report.md", false},
            {"quarantine.jsonl", false}, {"claim.json", false}, {"results.sarif", false},
            {"not-written-by-the-run.txt", false}, {"packs", true}, {"skill", true},
        };
        for (String command : List.of("assess", "quickstart")) {
            for (Object[] entry : entries) {
                cases.add(Arguments.of(command, entry[0], entry[1]));
            }
        }
        return cases.stream();
    }

    @ParameterizedTest(name = "{0} {1}")
    @MethodSource("linkedEntries")
    void aLinkInOutLeadingOutsideIsRefused(String command, String entry, boolean folder, @TempDir Path dir)
            throws IOException {
        Path outside = dir.resolve("outside");
        if (folder) {
            Files.createDirectory(outside);
            Files.writeString(outside.resolve("kept.txt"), "kept\n");
        } else {
            Files.writeString(outside, "kept\n");
        }
        Path out = Files.createDirectory(dir.resolve("out"));
        Files.createSymbolicLink(out.resolve(entry), outside);
        String[] argv = command.equals("assess") ? assess(out) : new String[] {"quickstart", "--out", out.toString()};
        JsonNode env = Fixtures.runJson(argv);
        assertRefused(env, "input.out_link_outside");
        assertTrue(env.get("error").get("detail").asText().contains(entry), env.toString());
        assertEquals(List.of(entry), files(out));
        if (folder) {
            assertEquals(List.of("kept.txt"), files(outside));
        } else {
            assertEquals("kept\n", Files.readString(outside));
        }
    }

    @Test
    void aDanglingLinkPointingOutsideIsRefused(@TempDir Path dir) throws IOException {
        Path out = Files.createDirectory(dir.resolve("out"));
        Files.createSymbolicLink(out.resolve("integrity.jsonl"), dir.resolve("missing.jsonl"));
        assertRefused(Fixtures.runJson("quickstart", "--out", out.toString()), "input.out_link_outside");
        assertFalse(Files.exists(dir.resolve("missing.jsonl")));
    }

    @Test
    void aLinkInsideOutIsFollowed(@TempDir Path dir) throws IOException {
        Path out = Files.createDirectory(dir.resolve("out"));
        Files.writeString(out.resolve("notes.txt"), "mine\n");
        Files.createSymbolicLink(out.resolve("notes-link.txt"), out.resolve("notes.txt"));
        JsonNode env = Fixtures.runJson("quickstart", "--out", out.toString());
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
        assertTrue(Files.isSymbolicLink(out.resolve("notes-link.txt")));
    }
}
