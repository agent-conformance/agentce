package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.io.TempDir;

/**
 * Every case in {@code spec/cli/argv-cases.json} (SPEC 8.5, "Command-line grammar") through the real
 * scanner and the real CLI entry: exit code and key as the table says, the parsed values of an accept
 * case, and a refused run that leaves its report directory byte-identical (18.184w). Mirrors Python's
 * {@code tests/test_argv_cases.py}. The CLI runs in-process, so relative paths in a case resolve against
 * the test's working directory (the JVM cannot change it); no case reads or writes a relative path that
 * exists.
 */
class ArgvCasesTest {
    private static final Path REPO = TestPaths.repoRoot();
    private static final JsonNode CASES = Json.parseFile(REPO.resolve("spec/cli/argv-cases.json")).get("cases");
    /** The key of a text-mode refusal: the first stdout line, {@code <key>: <reason>}. */
    private static final Pattern KEY = Pattern.compile("^([a-z][a-z0-9_]*(?:\\.[a-z0-9_]+)+): ");

    @TempDir
    static Path root;

    private static Map<String, String> places;

    @BeforeAll
    static void places() throws IOException {
        Path report = root.resolve("report");
        assertEquals(0, run(List.of("quickstart", "--out", report.toString())).code());
        Path space = root.resolve("sp ace");
        copyTree(REPO.resolve("spec/catalogs/base/eu-ai-act"), space.resolve("cat"));
        Files.writeString(space.resolve("gaps.md"), "");
        places = new LinkedHashMap<>();
        places.put("{report}", report.toString());
        places.put("{space}", space.toString());
    }

    private record Run(int code, String out) {}

    private static Run run(List<String> argv) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        PrintStream originalOut = System.out;
        PrintStream originalErr = System.err;
        System.setOut(new PrintStream(out, true, StandardCharsets.UTF_8));
        System.setErr(new PrintStream(err, true, StandardCharsets.UTF_8));
        int code;
        try {
            code = Cli.run(argv.toArray(String[]::new));
        } finally {
            System.setOut(originalOut);
            System.setErr(originalErr);
        }
        return new Run(code, out.toString(StandardCharsets.UTF_8));
    }

    private static String fill(String value) {
        for (Map.Entry<String, String> place : places.entrySet()) {
            value = value.replace(place.getKey(), place.getValue());
        }
        return value;
    }

    private static List<String> fill(JsonNode values) {
        List<String> filled = new ArrayList<>();
        values.forEach(v -> filled.add(fill(v.asText())));
        return filled;
    }

    private static boolean runsInJava(JsonNode c) {
        if (!c.has("engines")) {
            return true;
        }
        for (JsonNode engine : c.get("engines")) {
            if (engine.asText().equals("java")) {
                return true;
            }
        }
        return false;
    }

    @TestFactory
    Stream<DynamicTest> everyCaseThroughTheCli() {
        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : CASES) {
            tests.add(DynamicTest.dynamicTest(c.get("id").asText(), () -> checkCase(c)));
        }
        return tests.stream();
    }

    private static void checkCase(JsonNode c) {
        List<String> line = fill(c.get("argv"));
        Path unchanged = c.has("unchanged") ? Path.of(fill(c.get("unchanged").asText())) : null;
        Map<String, String> before = unchanged == null ? null : digest(unchanged);
        Run result = run(line);
        List<String> head = line.contains("--") ? line.subList(0, line.indexOf("--")) : line;
        String key = null;
        if (head.contains("--json") && result.code() != 0) {
            key = Json.parse(result.out()).get("error").get("message_key").asText();
        } else if (result.code() != 0) {
            Matcher found = KEY.matcher(result.out());
            key = found.find() ? found.group(1) : null;
        }
        if (unchanged != null) {
            assertEquals(before, digest(unchanged));
        }
        if (!runsInJava(c)) {
            List<String> json = new ArrayList<>(List.of("--json"));
            json.addAll(line);
            Run other = run(json);
            String otherKey = Json.parse(other.out()).get("error").get("message_key").asText();
            assertEquals(List.of(3, "cli.not_implemented"), List.of(other.code(), otherKey), other.out());
            return;
        }
        JsonNode wantKey = c.get("key");
        assertEquals(
                java.util.Arrays.asList(c.get("exit").asInt(), wantKey.isNull() ? null : wantKey.asText()),
                java.util.Arrays.asList(result.code(), key),
                result.out());
        if (c.get("rule").asText().equals("help_after_command")) {
            assertTrue(result.out().startsWith("usage: agentce"), result.out());
        }
    }

    @TestFactory
    Stream<DynamicTest> acceptCaseReadsItsValues() {
        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : CASES) {
            if (c.has("scan") && runsInJava(c)) {
                tests.add(DynamicTest.dynamicTest(c.get("id").asText(), () -> checkScan(c)));
            }
        }
        return tests.stream();
    }

    private static void checkScan(JsonNode c) {
        List<String> line = fill(c.get("argv"));
        int at = 0;
        while (line.get(at).startsWith("-")) {
            at++;
        }
        Argv.Scan read = Argv.scan(
                Argv.command(line.get(at)), line.subList(at + 1, line.size()), line.subList(0, at), null, null);
        c.get("scan").fields().forEachRemaining(field -> {
            JsonNode want = field.getValue();
            switch (field.getKey()) {
                case "values" -> {
                    Map<String, String> values = new LinkedHashMap<>();
                    want.fields().forEachRemaining(e -> values.put(e.getKey(), fill(e.getValue().asText())));
                    assertEquals(values, read.values, "values");
                }
                case "lists" -> {
                    Map<String, List<String>> lists = new LinkedHashMap<>();
                    want.fields().forEachRemaining(e -> lists.put(e.getKey(), fill(e.getValue())));
                    assertEquals(lists, read.lists, "lists");
                }
                case "positionals" -> assertEquals(fill(want), read.positionals, "positionals");
                case "flags" -> assertEquals(new LinkedHashSet<>(fill(want)), read.flags, "flags");
                default -> throw new AssertionError("unknown scan field " + field.getKey());
            }
        });
    }

    @Test
    void everyRuleHasACase() {
        Set<String> rules = new LinkedHashSet<>();
        Argv.grammar().get("rules").forEach(r -> rules.add(r.asText()));
        Set<String> covered = new LinkedHashSet<>();
        Set<String> words = new LinkedHashSet<>();
        for (JsonNode c : CASES) {
            covered.add(c.get("rule").asText());
            c.get("argv").forEach(t -> {
                if (!t.asText().startsWith("-")) {
                    words.add(t.asText());
                }
            });
        }
        assertEquals(rules, covered);
        Argv.grammar().get("commands").fieldNames().forEachRemaining(
                name -> assertTrue(words.contains(name), "no case names " + name));
    }

    @Test
    void vendoredGrammarIsTheSpecFile() throws IOException {
        assertEquals(
                Files.readString(REPO.resolve("spec/cli/argv-grammar.json")),
                Files.readString(REPO.resolve("engines/java/src/main/resources/cli/argv-grammar.json")));
    }

    private static Map<String, String> digest(Path dir) {
        Map<String, String> digests = new TreeMap<>();
        try (Stream<Path> walk = Files.walk(dir)) {
            for (Path p : (Iterable<Path>) walk.filter(Files::isRegularFile)::iterator) {
                digests.put(dir.relativize(p).toString(),
                        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(p))));
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        return digests;
    }

    private static void copyTree(Path from, Path to) throws IOException {
        try (Stream<Path> walk = Files.walk(from)) {
            for (Path p : (Iterable<Path>) walk::iterator) {
                Path target = to.resolve(from.relativize(p).toString());
                if (Files.isDirectory(p)) {
                    Files.createDirectories(target);
                } else {
                    Files.copy(p, target);
                }
            }
        }
    }
}
