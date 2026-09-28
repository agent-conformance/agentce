package org.agentce;

import java.io.IOException;
import java.io.InputStream;
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

    /** Every {@code group:artifact} coordinate line's normalised group and artifact names -- shared by
     * {@link #packageNames} (a {@code gradle.lockfile} on disk) and the installed-artifact self-report
     * below (a generated runtime-deps resource baked into the runnable jar), so both parse the exact
     * same {@code group:artifact[:version][=configurations]} shape. */
    private static Set<String> namesFromCoordinateLines(Iterable<String> lines) {
        Set<String> names = new LinkedHashSet<>();
        for (String raw : lines) {
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
        return names;
    }

    /** Every locked dependency's group and artifact names, normalised, from a {@code gradle.lockfile}. */
    private static Set<String> packageNames(Path lockPath) {
        try {
            return namesFromCoordinateLines(Files.readAllLines(lockPath, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read dependency lock: " + e.getMessage(), e);
        }
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

    public static final class InstalledResult {
        public final String result;
        public final List<String> denylistedPresent;

        InstalledResult(String result, List<String> denylistedPresent) {
            this.result = result;
            this.denylistedPresent = denylistedPresent;
        }
    }

    private static String readClasspathResource(String path) {
        try (InputStream in = NoMl.class.getResourceAsStream(path)) {
            if (in == null) {
                throw new IllegalStateException("missing classpath resource " + path);
            }
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException("cannot read classpath resource " + path + ": " + e.getMessage(), e);
        }
    }

    /** The vendored denylist copy an installed jar carries ({@code no-ml-denylist.txt} on the
     * classpath, byte-identical to {@code spec/rules/no-ml-denylist.txt}, {@code NoMlAndSchemaTest}
     * holds them in sync), for the installed self-report below -- unlike {@link #loadDenylist}, this
     * needs no repo checkout. */
    public static Set<String> loadVendoredDenylist() {
        Set<String> out = new LinkedHashSet<>();
        for (String raw : readClasspathResource("/no-ml-denylist.txt").split("\n")) {
            String line = raw.split("#", 2)[0].trim();
            if (!line.isEmpty()) {
                out.add(normalize(line));
            }
        }
        return out;
    }

    /** The real, bundled runtime dependency set an installed jar carries: normalised group and
     * artifact names baked into the jar at build time by the {@code noMlRuntimeDeps} Gradle task (from
     * {@code configurations.runtimeClasspath}'s resolved artifacts, {@code no-ml-runtime-deps.txt} on
     * the classpath) -- the actual dependency tree the running jar has, not a lockfile an installed
     * artifact cannot see. */
    public static Set<String> loadRuntimeDeps() {
        return namesFromCoordinateLines(List.of(readClasspathResource("/no-ml-runtime-deps.txt").split("\n")));
    }

    /** The installed engine's own {@code no_ml} self-report ({@code agentce version --json}): every
     * name in {@code denylist} that is actually present in {@code deps} -- the running jar's real,
     * bundled dependency set -- intersected by the same PEP-503-style normalisation this class already
     * uses for the repo-checkout scan above (parametrised so a unit test can supply a synthetic
     * denylist; mirrors the TypeScript port's {@code evaluateInstalledNoMl}). */
    public static InstalledResult evaluateInstalled(Set<String> denylist, Set<String> deps) {
        List<String> present = new ArrayList<>();
        for (String d : denylist) {
            if (deps.contains(d)) {
                present.add(d);
            }
        }
        present.sort(Json::byteCompare);
        return new InstalledResult(present.isEmpty() ? "pass" : "fail", present);
    }
}
