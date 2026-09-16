package org.agentce;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * The no-learned-components check for the Java engine (SPEC §8.7, HR-1/HR-2). The engine and every
 * evaluation-path dependency must resolve to a tree with no machine-learning framework and no LLM or
 * embedding client. This scans the engine's own {@code gradle.lockfile} for any dependency whose group
 * or artifact matches an entry in the shared denylist {@code spec/rules/no-ml-denylist.txt}, compared
 * by exact PEP 503 normalisation (lowercase; runs of {@code - _ .} collapsed to one {@code -}), never
 * as a substring. {@code claim: full} requires {@code result: pass}.
 */
public final class NoMl {
    private NoMl() {}

    public static final class Result {
        public final String result;
        public final int packages;
        public final List<String> violations;

        Result(String result, int packages, List<String> violations) {
            this.result = result;
            this.packages = packages;
            this.violations = violations;
        }
    }

    private static String normalize(String name) {
        return name.trim().toLowerCase(Locale.ROOT).replaceAll("[-_.]+", "-");
    }

    private static Set<String> loadDenylist(Path repoRoot) {
        Set<String> out = new LinkedHashSet<>();
        Path denyFile = repoRoot.resolve("spec/rules/no-ml-denylist.txt");
        try {
            for (String raw : Files.readAllLines(denyFile, StandardCharsets.UTF_8)) {
                String line = raw.split("#", 2)[0].trim();
                if (!line.isEmpty()) {
                    out.add(normalize(line));
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("cannot read no-ml denylist: " + e.getMessage(), e);
        }
        return out;
    }

    /** Every locked dependency's group and artifact names, normalised, from a {@code gradle.lockfile}. */
    private static Set<String> packageNames(Path lockPath) {
        Set<String> names = new LinkedHashSet<>();
        try {
            for (String raw : Files.readAllLines(lockPath, StandardCharsets.UTF_8)) {
                String line = raw.strip();
                if (line.isEmpty() || line.startsWith("#")) {
                    continue;
                }
                int eq = line.indexOf('=');
                String coord = eq >= 0 ? line.substring(0, eq) : line;
                String[] parts = coord.split(":");
                if (parts.length >= 2) {
                    names.add(normalize(parts[0])); // group
                    names.add(normalize(parts[1])); // artifact
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("cannot read dependency lock: " + e.getMessage(), e);
        }
        return names;
    }

    /** Scan the engine's lockfile against the denylist; {@code result: pass} iff no denylisted name is present. */
    public static Result evaluate(Path repoRoot, Path lockPath) {
        Set<String> deny = loadDenylist(repoRoot);
        Set<String> names = packageNames(lockPath);
        List<String> violations = new ArrayList<>();
        for (String name : names) {
            if (deny.contains(name)) {
                violations.add(name);
            }
        }
        violations.sort(Json::byteCompare);
        return new Result(violations.isEmpty() ? "pass" : "fail", names.size(), violations);
    }
}
