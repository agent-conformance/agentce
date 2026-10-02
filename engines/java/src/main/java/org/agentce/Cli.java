package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.TreeSet;
import java.util.stream.Stream;

/**
 * The {@code agentce} command-line interface (SPEC §8.5): the same {@code --json} envelope and
 * exit-code scheme as the reference. {@code assess}, {@code validate}, {@code report}, and {@code
 * quickstart} are implemented on the existing ECS/report path (the same modules {@code conformance run}
 * already exercises); any other verb returns a stable {@code input_error} envelope rather than a guess.
 */
public final class Cli {
    private Cli() {}

    private static final String DEFAULT_OUT_DIR = "out";
    /** The catalog an assessment evaluates when nothing names one: the cross-standard baseline. */
    private static final String DEFAULT_LENS = "baseline@2026.09";
    private static final List<String> REPORT_FORMATS = List.of("md", "html", "oscal", "sarif", "pack");
    private static final Set<String> VERDICT_OUTCOMES = Set.of("conformant", "non-conformant", "insufficient_evidence");
    /** The same four flags Python's own top-level parser accepts for every command ({@code
     * --debug}/{@code --quiet} are silently ignored here too, exactly as they are everywhere else in
     * both engines today) -- skipped by {@link #positionalArgs}, never read as a positional. */
    private static final Set<String> GLOBAL_BOOLEAN_FLAGS = Set.of("--json", "--debug", "--quiet");

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        String command = args.length > 0 ? args[0] : null;
        if ("--version".equals(command) || "-V".equals(command)) {
            System.out.println("agentce " + Version.ENGINE_VERSION);
            return 0;
        }

        // The numerics verb is a plain computation seam for the cross-engine vector check: it reads a
        // numerics-vectors case file and prints {caseName: result} as plain JSON, not the envelope.
        if ("numerics".equals(command)) {
            if (args.length < 2) {
                System.err.println("numerics: a case file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            System.out.println(Json.pretty(Numerics.computeVectorFile(java.nio.file.Path.of(args[1]))));
            return 0;
        }

        // digest-tree is a plain computation seam (the same pattern as `numerics` above), driven from
        // outside the repo's Java sources by `tools/catalog_digest_check.py` against the built jar: it
        // prints one catalog directory's real content digest, nothing else.
        if ("digest-tree".equals(command)) {
            if (args.length < 2) {
                System.err.println("digest-tree: a directory path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            System.out.println(Catalog.provenanceDigest(Paths.get(args[1])));
            return 0;
        }

        // security-view is a plain computation seam (the same pattern as `numerics`/`digest-tree`
        // above), driven by the security-view build gate's cross-engine diff (18.16, C5): it reads a
        // fixture file with {activity, assertions}, runs SecurityView.compute, and prints the result
        // as JSON -- not part of the public `assess` command surface (no engine here has
        // `--for`/multi-format `write_report` yet, TRADEOFFS row 8).
        if ("security-view".equals(command)) {
            if (args.length < 2) {
                System.err.println("security-view: a fixture file path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            JsonNode data = Json.parseFile(Paths.get(args[1]));
            ObjectNode activity = (ObjectNode) data.get("activity");
            List<Assertions.Assertion> assertions = new ArrayList<>();
            for (JsonNode a : data.get("assertions")) {
                assertions.add(Assertions.fromJson(a));
            }
            System.out.println(SecurityView.compute(activity, assertions).toString());
            return 0;
        }

        // otel-genai-fixture is a plain computation seam (the same pattern as `security-view`
        // above), driven by the otel-genai adapter's cross-engine parity check (18.29, C3/C4): it
        // reads `<dir>/input.json` and `<dir>/adapt.json` the same way the Python reference's
        // fixtures module does, calls `adapt`, and for each emitted event (in the adapter's own
        // pinned time/id order) attempts `canonicalString`; on success prints `EVENT <result>`. On
        // a `CanonicalizationError`, nothing further goes to stdout -- `ERROR canonical:<reason>`
        // goes to stderr and the process exits 1, so the check can tell "the adapter accepted this
        // but the event cannot be serialised" apart from "the adapter refused the whole document"
        // (an `OtelGenaiAdapterError`, printed the same way without the `canonical:` prefix). On a
        // clean run, one final `REPORT <json>` line follows every `EVENT` line.
        if ("otel-genai-fixture".equals(command)) {
            if (args.length < 2) {
                System.err.println("otel-genai-fixture: a directory path is required");
                return ExitCode.INPUT_ERROR.code;
            }
            Path dir = Paths.get(args[1]);
            byte[] inputBytes;
            JsonNode adaptArgs;
            try {
                inputBytes = Files.readAllBytes(dir.resolve("input.json"));
                adaptArgs = Json.parseFile(dir.resolve("adapt.json"));
            } catch (IOException | RuntimeException e) {
                System.err.println("otel-genai-fixture: " + e.getMessage());
                return ExitCode.INPUT_ERROR.code;
            }
            String subject = adaptArgs.has("subject") ? adaptArgs.get("subject").asText() : null;
            String sourceClass = adaptArgs.has("source_class") && !adaptArgs.get("source_class").isNull()
                    ? adaptArgs.get("source_class").asText()
                    : null;
            String source = adaptArgs.has("source") && !adaptArgs.get("source").isNull()
                    ? adaptArgs.get("source").asText()
                    : null;
            try {
                OtelGenai.AdaptResult result = OtelGenai.adapt(inputBytes, subject, sourceClass, source);
                for (JsonNode event : result.events()) {
                    String line;
                    try {
                        line = Canonical.canonicalString(event);
                    } catch (Canonical.CanonicalizationError exc) {
                        System.err.println("ERROR canonical:" + exc.reason);
                        return 1;
                    }
                    System.out.println("EVENT " + line);
                }
                System.out.println("REPORT " + otelGenaiReportLine(result.report()));
                return 0;
            } catch (OtelGenai.OtelGenaiAdapterError exc) {
                System.err.println("ERROR " + exc.reason);
                return 1;
            }
        }

        boolean json = Arrays.asList(args).contains("--json");
        boolean debug = Arrays.asList(args).contains("--debug");
        CommandResult result;
        try {
            if ("conformance".equals(command)) {
                result = cmdConformance(args);
            } else if ("validate".equals(command)) {
                result = cmdValidate(args);
            } else if ("assess".equals(command)) {
                result = cmdAssess(args);
            } else if ("report".equals(command)) {
                result = cmdReport(args);
            } else if ("diff".equals(command)) {
                result = cmdDiff(args);
            } else if ("readiness".equals(command)) {
                result = cmdReadiness(args);
            } else if ("sign".equals(command)) {
                result = cmdSign(args);
            } else if ("verify".equals(command)) {
                result = cmdVerify(args);
            } else if ("quickstart".equals(command)) {
                result = cmdQuickstart(args);
            } else if ("version".equals(command)) {
                result = cmdVersion();
            } else {
                result = notImplemented(command == null ? "" : command);
            }
        } catch (AgentceError exc) {
            result = errorResult(command == null ? "" : command, exc);
        } catch (RuntimeException exc) {
            if (debug) {
                // Matches Python's own `--debug` behaviour (`cli.py:562-564`, `if debug: raise`): let
                // the exception propagate to the JVM's default handler (a full stack trace on
                // stderr), rather than swallowing it into the keyed envelope below.
                throw exc;
            }
            // A non-AgentceError exception here would otherwise propagate to the JVM's default
            // handler, which prints a stack trace and exits with code 1 -- colliding with
            // ExitCode.FINDINGS, so a malformed-input crash would be indistinguishable from a real
            // finding to a CI script gating on exit code. Matches Python's key, exit code, and (now
            // that `--debug` is real in this engine too) fix text -- not its full error envelope
            // shape (a real, pre-existing, engine-wide difference this does not close).
            String message = exc.getMessage() != null ? exc.getMessage() : exc.getClass().getName();
            result = errorResult(
                    command == null ? "" : command,
                    new AgentceError(
                            "internal.unexpected",
                            message,
                            "re-run with --debug to see the stack trace, then file an issue."));
        }
        emit(result, json);
        return result.exitCode();
    }

    private static void emit(CommandResult result, boolean json) {
        if (json) {
            System.out.println(Json.pretty(result.envelope()));
        } else {
            for (String line : result.humanLines) {
                System.out.println(line);
            }
        }
    }

    private static String flagValue(String[] args, String name) {
        int index = Arrays.asList(args).indexOf("--" + name);
        return index >= 0 && index + 1 < args.length ? args[index + 1] : null;
    }

    /** {@code null} stays {@code null}; an empty string becomes {@code null} too (Python's `if
     * value` truthiness check has no Java equivalent, so callers that need it call this explicitly). */
    private static String emptyToNull(String value) {
        return value == null || value.isEmpty() ? null : value;
    }

    /** Every value following a (repeatable) {@code --name} in {@code args}, in order. */
    private static List<String> flagValues(String[] args, String name) {
        List<String> out = new ArrayList<>();
        String flag = "--" + name;
        for (int i = 0; i < args.length; i++) {
            if (flag.equals(args[i]) && i + 1 < args.length) {
                out.add(args[i + 1]);
            }
        }
        return out;
    }

    private static final List<String> VERIFY_TARGETS = List.of("bundle", "catalog", "release", "report");

    private record VerifyArgs(Map<String, List<String>> targets, String signerTrustRoot, String expectKeyid) {}

    private static InputError verifyUnrecognized(String detail, String fix) {
        return new InputError("input.verify_unrecognized_flag", detail, fix);
    }

    /** {@code agentce verify}'s args, read the way Python's {@code verify} subparser (argparse,
     * {@code allow_abbrev=False}) reads them, so a command line gets one answer from all three engines
     * (18.65 round 3): the four target flags take one value each and are collected so {@link
     * #verifyTarget} can refuse a repeat; {@code --signer-trust-root}/{@code --expect-keyid} take one
     * value (the last one wins); {@code --json}/{@code --debug}/{@code --quiet} take none; a value is
     * the next token or the {@code =}-joined rest. A flag missing its value or given one it takes none
     * of refuses at once, as argparse does mid-parse; any other token (an unknown or abbreviated flag,
     * {@code --}, a positional) refuses after the scan, naming the first one, as argparse reports what
     * it left unparsed. */
    private static VerifyArgs parseVerifyArgs(String[] args) {
        Map<String, List<String>> targets = new LinkedHashMap<>();
        for (String target : VERIFY_TARGETS) {
            targets.put(target, new ArrayList<>());
        }
        String signerTrustRoot = null;
        String expectKeyid = null;
        String unknown = null;
        for (int i = 1; i < args.length; i++) {
            String token = args[i];
            if (!looksLikeOption(token) || token.equals("--")) {
                unknown = unknown == null ? token : unknown;
                continue;
            }
            int eq = token.indexOf('=');
            String name = eq >= 0 ? token.substring(0, eq) : token;
            if (GLOBAL_BOOLEAN_FLAGS.contains(name)) {
                if (eq >= 0) {
                    throw verifyUnrecognized("flag '" + name + "' takes no value.", "drop the value: " + name + ".");
                }
                continue;
            }
            String flag = name.startsWith("--") ? name.substring(2) : "";
            boolean isTarget = VERIFY_TARGETS.contains(flag);
            if (!isTarget && !flag.equals("signer-trust-root") && !flag.equals("expect-keyid")) {
                unknown = unknown == null ? token : unknown;
                continue;
            }
            String value;
            if (eq >= 0) {
                value = token.substring(eq + 1);
            } else {
                if (i + 1 >= args.length || looksLikeOption(args[i + 1])) {
                    throw verifyUnrecognized("flag '" + name + "' needs a value.", "pass " + name + " <value>.");
                }
                value = args[++i];
            }
            if (isTarget) {
                targets.get(flag).add(value);
            } else if (flag.equals("signer-trust-root")) {
                signerTrustRoot = value;
            } else {
                expectKeyid = value;
            }
        }
        if (unknown != null) {
            if (unknown.startsWith("-") && !unknown.equals("-")) {
                throw verifyUnrecognized(
                        "unrecognized flag '" + unknown + "'.",
                        "pass --bundle, --catalog, --release, or --report (with --signer-trust-root or "
                                + "--expect-keyid for --report), or drop the flag.");
            }
            throw verifyUnrecognized(
                    "unrecognized argument '" + unknown + "'.",
                    "pass the target with its flag, e.g. `agentce verify --bundle <dir>`.");
        }
        return new VerifyArgs(targets, signerTrustRoot, expectKeyid);
    }

    /** One target flag's value: refuses a repeated {@code --name} (Python's {@code _verify_target_flag}
     * too, verifier round 2, 18.65) and normalizes an empty value to {@code null} (F4, 18.65). */
    private static String verifyTarget(VerifyArgs parsed, String name) {
        List<String> values = parsed.targets().get(name);
        if (values.size() > 1) {
            throw new InputError(
                    "input.verify_target",
                    "--" + name + " was given more than once.",
                    "pass --" + name + " <path> once.");
        }
        return emptyToNull(values.isEmpty() ? null : values.get(0));
    }

    private static String requireDir(String raw, String key, String what) {
        return requireDir(raw, key, what, "pass --" + key + " <dir>.");
    }

    /** {@link #requireDir}, with an explicit {@code fix} rather than the computed {@code --key}-style
     * default -- {@code readiness}'s positional {@code report_dir} (item 18.25) needs its own fix text,
     * mirroring {@link #requireFile}'s existing 4-arg overload. */
    private static String requireDir(String raw, String key, String what, String fix) {
        if (raw == null) {
            throw new InputError("input." + key + "_missing", what + " is required.", fix);
        }
        Path path = Paths.get(raw);
        if (!Files.isDirectory(path)) {
            throw new InputError(
                    "input." + key + "_not_a_directory", what + " '" + raw + "' is not an existing directory.", fix);
        }
        return raw;
    }

    private static String requireFile(String raw, String key, String what) {
        return requireFile(raw, key, what, "pass --" + key + " <file>.");
    }

    /** {@link #requireFile}, with an explicit {@code fix} rather than the computed {@code --key}-style
     * default -- {@code diff}'s two positional arguments (item 18.24) need diff-specific fix text. */
    private static String requireFile(String raw, String key, String what, String fix) {
        if (raw == null) {
            throw new InputError("input." + key + "_missing", what + " is required.", fix);
        }
        Path path = Paths.get(raw);
        if (!Files.isRegularFile(path)) {
            throw new InputError(
                    "input." + key + "_not_a_file", what + " '" + raw + "' is not an existing file.", fix);
        }
        return raw;
    }

    /** A hardened positional-argument scanner for {@code diff} (the first CLI verb in this engine with
     * positional, not {@code --flag}, arguments). {@code args[0]} is the command name and is not itself
     * scanned. Skips exactly {@link #GLOBAL_BOOLEAN_FLAGS} and {@code --format <value>}, collecting every
     * other token in order, but throws {@code input.diff_unrecognized_flag} on any other {@code
     * --}-prefixed token instead of silently reading it as a positional. {@code --format=<value>}
     * (single-token, {@code =}-joined) is out of scope, matching every other flag in both engines
     * today. */
    private static List<String> positionalArgs(String[] args) {
        List<String> out = new ArrayList<>();
        for (int i = 1; i < args.length; i++) {
            String token = args[i];
            if (GLOBAL_BOOLEAN_FLAGS.contains(token)) {
                continue;
            }
            if ("--format".equals(token)) {
                i++; // also skip the value token, if any
                continue;
            }
            if (token.startsWith("--")) {
                throw new InputError(
                        "input.diff_unrecognized_flag",
                        "unrecognized flag '" + token + "'.",
                        "pass --format text|json|md, or drop the flag.");
            }
            out.add(token);
        }
        return out;
    }

    /**
     * A path safe to write into a shared artifact (SPEC §8.4: {@code invocation} records paths, never a
     * person or their local filesystem layout). Under the home directory it becomes {@code ~/…}; else
     * under the current directory it becomes a relative path; anywhere else only the final path
     * component is kept. Mirrors the Python reference's {@code _scrub_path}.
     */
    private static String scrubPath(String raw) {
        Path abs = Paths.get(raw).toAbsolutePath().normalize();
        Path home = Paths.get(System.getProperty("user.home")).toAbsolutePath().normalize();
        if (abs.equals(home) || abs.startsWith(home)) {
            String rel = home.relativize(abs).toString().replace(java.io.File.separatorChar, '/');
            return rel.isEmpty() ? "~/" : "~/" + rel;
        }
        Path cwd = Paths.get("").toAbsolutePath().normalize();
        if (abs.equals(cwd) || abs.startsWith(cwd)) {
            String rel = cwd.relativize(abs).toString().replace(java.io.File.separatorChar, '/');
            return rel.isEmpty() ? "." : rel;
        }
        Path fileName = abs.getFileName();
        return fileName != null ? fileName.toString() : abs.toString();
    }

    /** Write one canonical JSON object per line (no trailing newline when {@code records} is empty). */
    private static void writeJsonl(List<ObjectNode> records, Path path) {
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            StringBuilder out = new StringBuilder();
            for (ObjectNode record : records) {
                out.append(Canonical.canonicalString(record)).append('\n');
            }
            Files.write(path, out.toString().getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot write " + path + ": " + e.getMessage(), e);
        }
    }

    private static void writeQuarantineJsonl(List<Quarantine.Record> records, Path path) {
        List<ObjectNode> nodes = new ArrayList<>();
        for (Quarantine.Record r : records) {
            nodes.add(r.toJson());
        }
        writeJsonl(nodes, path);
    }

    /** Every catalog the engine ships (base and sector overlays), keyed {@code id@version}. */
    private static Map<String, Path> vendoredCatalogs() {
        Map<String, Path> found = new LinkedHashMap<>();
        Path root = Bundled.catalogsDir();
        for (String kind : new String[] {"base", "overlays"}) {
            Path dir = root.resolve(kind);
            List<String> names = new ArrayList<>();
            if (Files.isDirectory(dir)) {
                try (Stream<Path> stream = Files.list(dir)) {
                    stream.map(p -> p.getFileName().toString()).forEach(names::add);
                } catch (IOException ignored) {
                    // no vendored catalogs of this kind
                }
            }
            names.sort(Json::byteCompare);
            for (String name : names) {
                Path catalogYaml = dir.resolve(name).resolve("catalog.yaml");
                if (!Files.isRegularFile(catalogYaml)) {
                    continue;
                }
                try {
                    JsonNode meta = Yaml.parseFile(catalogYaml);
                    if (meta != null && meta.isObject() && meta.has("id") && meta.has("version")
                            && meta.get("id").isTextual() && meta.get("version").isTextual()) {
                        found.put(meta.get("id").asText() + "@" + meta.get("version").asText(), dir.resolve(name));
                    }
                } catch (RuntimeException ignored) {
                    // a malformed vendored catalog is skipped, never fatal to discovery
                }
            }
        }
        return found;
    }

    private record Resolved(List<Catalog> catalogs, List<String> labels) {}

    /**
     * The catalogs an assessment evaluates, and their {@code id@version} labels (mirrors the Python
     * reference's {@code _resolve_catalogs}). Each {@code --catalog-dir} is loaded as given. Each
     * requested id — the {@code --catalog} list, else the profile's declared {@code catalogs} when no
     * directory was passed — must resolve to a directory that was passed or to a vendored catalog. A
     * run that passes no {@code --catalog} and no {@code --catalog-dir}, whose profile declares no
     * catalogs, evaluates the baseline ({@link #DEFAULT_LENS}).
     */
    private static Resolved resolveCatalogs(String requested, Profile profile, List<String> catalogDirs) {
        List<Catalog> loaded = new ArrayList<>();
        for (String d : catalogDirs) {
            loaded.add(Catalog.load(Paths.get(requireDir(d, "catalog-dir", "the catalog directory"))));
        }
        Map<String, Catalog> byLabel = new LinkedHashMap<>();
        for (Catalog c : loaded) {
            byLabel.put(c.id + "@" + c.version, c);
        }
        List<String> ids;
        if (requested != null && !requested.isEmpty()) {
            ids = new ArrayList<>();
            for (String s : requested.split(",")) {
                String trimmed = s.strip();
                if (!trimmed.isEmpty()) {
                    ids.add(trimmed);
                }
            }
        } else if (!catalogDirs.isEmpty()) {
            ids = new ArrayList<>();
        } else {
            ids = new ArrayList<>(profile.catalogs);
            if (ids.isEmpty() && requested == null) {
                ids.add(DEFAULT_LENS);
            }
        }
        if (ids.isEmpty() && loaded.isEmpty()) {
            throw new InputError(
                    "input.catalog_missing",
                    "--catalog was given but names no catalog.",
                    "pass --catalog <id@version>, or leave --catalog out to assess against the baseline.");
        }
        List<String> uniqueIds = new ArrayList<>(new LinkedHashSet<>(ids));
        List<String> unresolved = new ArrayList<>();
        for (String i : uniqueIds) {
            if (!byLabel.containsKey(i)) {
                unresolved.add(i);
            }
        }
        if (!unresolved.isEmpty()) {
            Map<String, Path> vendored = vendoredCatalogs();
            List<String> stillUnresolved = new ArrayList<>();
            for (String u : unresolved) {
                if (vendored.containsKey(u)) {
                    byLabel.put(u, Catalog.load(vendored.get(u)));
                } else {
                    stillUnresolved.add(u);
                }
            }
            unresolved = stillUnresolved;
        }
        if (!unresolved.isEmpty()) {
            Map<String, Path> vendored = vendoredCatalogs();
            TreeSet<String> available = new TreeSet<>(Json::byteCompare);
            available.addAll(byLabel.keySet());
            available.addAll(vendored.keySet());
            List<String> quoted = new ArrayList<>();
            for (String u : unresolved) {
                quoted.add("'" + u + "'");
            }
            throw new InputError(
                    "input.catalog_unresolved",
                    "no catalog directory resolves " + String.join(", ", quoted)
                            + " (available: " + (available.isEmpty() ? "none" : String.join(", ", available)) + ").",
                    "use an available <id>@<version>, or pass --catalog-dir <dir> for a catalog on disk.");
        }
        LinkedHashSet<String> labelSet = new LinkedHashSet<>(ids);
        for (Catalog c : loaded) {
            labelSet.add(c.id + "@" + c.version);
        }
        List<String> labels = new ArrayList<>(labelSet);
        List<Catalog> catalogs = new ArrayList<>();
        for (String l : labels) {
            catalogs.add(byLabel.get(l));
        }
        return new Resolved(catalogs, labels);
    }

    private static boolean evaluatedNothing(List<Assertions.Assertion> assertions) {
        for (Assertions.Assertion a : assertions) {
            if (VERDICT_OUTCOMES.contains(a.outcome)) {
                return false;
            }
        }
        return true;
    }

    private static String ids(List<String> list, int cap) {
        StringBuilder shown = new StringBuilder();
        int n = Math.min(list.size(), cap);
        for (int i = 0; i < n; i++) {
            if (i > 0) {
                shown.append(", ");
            }
            String item = list.get(i);
            shown.append('\'').append(item.length() > 80 ? item.substring(0, 80) : item).append('\'');
        }
        String result = shown.length() > 0 ? shown.toString() : "none";
        return list.size() > cap ? result + " and " + (list.size() - cap) + " more" : result;
    }

    /** The exit-3 error for a run whose every (control, subject) pair was inapplicable or unassessed. */
    private static InputError nothingEvaluated(Profile profile, List<JsonNode> accepted, int pairs) {
        TreeSet<String> declared = new TreeSet<>(Json::byteCompare);
        for (Profile.Subject s : profile.subjects) {
            declared.add(s.id);
        }
        TreeSet<String> seen = new TreeSet<>(Json::byteCompare);
        for (JsonNode e : accepted) {
            if (e.has("subject")) {
                seen.add(e.get("subject").asText());
            }
        }
        List<String> matched = new ArrayList<>();
        for (String s : declared) {
            if (seen.contains(s)) {
                matched.add(s);
            }
        }
        String why;
        if (accepted.isEmpty()) {
            why = "the bundle has no accepted events";
        } else if (matched.isEmpty()) {
            why = "none of the " + accepted.size() + " accepted events is about a subject the profile declares "
                    + "(profile: " + ids(new ArrayList<>(declared), 3) + "; bundle: " + ids(new ArrayList<>(seen), 3) + ")";
        } else {
            why = "the " + accepted.size() + " accepted events give no control an applicable population "
                    + "(subjects matched: " + ids(matched, 3) + ")";
        }
        return new InputError(
                "input.nothing_evaluated",
                "assessed " + pairs + " (control, subject) pairs and none reached conformant, non-conformant, or "
                        + "insufficient_evidence: " + why + ". The reports were written, but they judge nothing.",
                "emit under the subject and source the profile declares, and record the evidence the "
                        + "catalog's controls apply to.");
    }

    private static CommandResult cmdConformance(String[] args) {
        CommandResult result = new CommandResult("conformance");
        String action = args.length > 1 ? args[1] : null;
        if (!"run".equals(action)) {
            throw new InputError(
                    "input.conformance_action", "the only conformance action is `run`.", "run `agentce conformance run ...`.");
        }
        String engine = requireDir(flagValue(args, "engine"), "engine", "the engine path");
        String corpus = requireDir(flagValue(args, "corpus"), "corpus", "the corpus directory");
        String out = flagValue(args, "out");
        ObjectNode report = Conformance.runEcs(Paths.get(engine), Paths.get(corpus), out == null ? null : Paths.get(out));

        result.data.put("action", "run");
        result.data.put("engine", engine);
        result.data.put("corpus", corpus);
        if (out != null) {
            result.data.put("out", out);
        }
        result.data.setAll(report); // report.engine (impl/version) overwrites the engine path, as in the reference
        result.note("ECS: " + report.get("projects").get("identical").asInt() + "/"
                + report.get("projects").get("total").asInt() + " identical; claim " + report.get("claim").asText()
                + "; no_ml " + report.get("no_ml").asText());
        if (!"full".equals(report.get("claim").asText())) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        return result;
    }

    private static CommandResult cmdValidate(String[] args) {
        CommandResult result = new CommandResult("validate");
        String bundleDir = requireDir(flagValue(args, "bundle"), "bundle", "the evidence bundle");
        Bundle bundle = Bundle.load(Paths.get(bundleDir)); // raises InputError (exit 3) on a missing/mismatching manifest
        Ingest.Result ingested = Ingest.ingest(bundle);
        result.data.put("bundle", bundleDir);
        result.data.put("bundle_digest", bundle.digest());
        result.data.put("accepted", ingested.accepted.size());
        result.data.put("quarantined", ingested.quarantined.size());
        ObjectNode quarantineByReason = result.data.putObject("quarantine_by_reason");
        for (Map.Entry<String, Integer> e : Quarantine.countsByReason(ingested.quarantined).entrySet()) {
            quarantineByReason.put(e.getKey(), e.getValue());
        }
        String out = flagValue(args, "out");
        if (out != null) {
            Path quarantinePath = Paths.get(out, "quarantine.jsonl");
            writeQuarantineJsonl(ingested.quarantined, quarantinePath);
            result.data.put("quarantine_file", quarantinePath.toString());
        }
        result.note("validated " + bundleDir + ": " + ingested.accepted.size() + " accepted, "
                + ingested.quarantined.size() + " quarantined");
        if (!ingested.quarantined.isEmpty()) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        return result;
    }

    private record AssessOptions(
            String bundle,
            String profile,
            String catalog,
            String domain,
            List<String> catalogDirs,
            String out,
            String state,
            String invocationCommand) {}

    /** Run a full assessment (ingest, integrity, graph, coverage, applicability, evaluate, report) — the
     * same pipeline {@code conformance run} exercises per corpus project, generalised to an arbitrary
     * bundle. */
    private static CommandResult runAssess(AssessOptions options) {
        CommandResult result = new CommandResult("assess");
        String bundleDir = requireDir(options.bundle(), "bundle", "the evidence bundle");
        String profilePath = requireFile(options.profile(), "profile", "the applicability profile");
        Path out = Paths.get(options.out());
        Profile profileObj = Profile.load(Paths.get(profilePath));
        Resolved resolved = resolveCatalogs(options.catalog(), profileObj, options.catalogDirs());

        // Stage 1: ingest and validate. A missing/mismatching manifest aborts with exit 3.
        Bundle bundle = Bundle.load(Paths.get(bundleDir));
        Ingest.Result ingested = Ingest.ingest(bundle);
        writeQuarantineJsonl(ingested.quarantined, out.resolve("quarantine.jsonl"));

        // Stage 2: integrity verification, one IntegrityResult per stream.
        List<Integrity.Result> integrityResults = Integrity.verifyBundle(ingested.accepted, bundle.manifest, bundle.root);
        List<ObjectNode> integrityNodes = new ArrayList<>();
        for (Integrity.Result r : integrityResults) {
            integrityNodes.add(r.toJson());
        }
        writeJsonl(integrityNodes, out.resolve("integrity.jsonl"));

        // Stage 3: build the provenance graph (in-memory store).
        DomainBinding domain = options.domain() != null
                ? DomainBinding.load(Paths.get(requireFile(options.domain(), "domain", "the domain binding")))
                : DomainBinding.empty();
        GraphStore store = Graph.buildGraph(ingested.accepted, domain);
        int graphTriples = store.tripleCount();

        // Stage 4: coverage and reconciliation against independent denominators.
        ObjectNode coverage = Coverage.computeCoverage(ingested.accepted, profileObj, Paths.get(bundleDir));
        try {
            Files.createDirectories(out);
            Files.write(out.resolve("coverage.json"), (Json.pretty(coverage) + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot write coverage.json: " + e.getMessage(), e);
        }

        // Stage 5: applicability resolution and drift.
        ArrayNode statements = Applicability.resolve(profileObj, ingested.accepted, List.of());
        List<ObjectNode> statementNodes = new ArrayList<>();
        for (JsonNode s : statements) {
            statementNodes.add((ObjectNode) s);
        }
        writeJsonl(statementNodes, out.resolve("applicability.jsonl"));
        int driftFindings = 0;
        for (JsonNode s : statements) {
            JsonNode drift = s.get("drift");
            if (drift != null && drift.isArray()) {
                driftFindings += drift.size();
            }
        }

        // Stage 6: catalog evaluation and report artifacts.
        List<Assertions.Assertion> evaluated =
                Assess.assessSubjects(ingested.accepted, profileObj, resolved.catalogs(), domain);

        // Stage 6a: incremental state (SPEC §5.4 B7, HR-10).
        String newWindowEnd = StateDir.windowEnd(profileObj.observationWindow, ingested.accepted);
        StateDir state = null;
        List<String> supersedes = List.of();
        if (options.state() != null) {
            state = StateDir.load(Paths.get(options.state())); // incompatible state_version aborts with exit 3
            supersedes = state.plan(bundle.digest(), ingested.accepted, newWindowEnd);
        }

        String operatorEnv = System.getenv("AGENTCE_OPERATOR");
        List<String> invocation = List.of(options.invocationCommand(), scrubPath(bundleDir), scrubPath(profilePath));
        ObjectNode activity = Activity.summarizeActivity(ingested.accepted, profileObj);
        ObjectNode blindSpots = BlindSpots.computeBlindSpots(evaluated, profileObj, resolved.catalogs(), ingested.accepted);
        Report.writeReport(
                out, evaluated, bundle.digest(), resolved.labels(),
                operatorEnv != null ? operatorEnv : "unknown",
                invocation, supersedes, Messages.DEFAULT_LANGUAGE, resolved.catalogs(), activity, blindSpots,
                profileObj, null, ingested.accepted);
        if (state != null) {
            state.record(bundle.digest(), out.resolve("manifest.json"), newWindowEnd);
        }

        int nonConformant = 0;
        for (Assertions.Assertion a : evaluated) {
            if ("non-conformant".equals(a.outcome)) {
                nonConformant++;
            }
        }
        Verdict.Summary summary = Verdict.summarize(evaluated);
        result.data.put("bundle", bundleDir);
        result.data.put("bundle_digest", bundle.digest());
        result.data.put("profile", profilePath);
        ArrayNode catalogsArr = result.data.putArray("catalogs");
        resolved.labels().forEach(catalogsArr::add);
        result.data.put("out", out.toString());
        result.data.put("accepted", ingested.accepted.size());
        result.data.put("quarantined", ingested.quarantined.size());
        result.data.put("streams", integrityResults.size());
        result.data.put("graph_triples", graphTriples);
        result.data.put("subjects", ((ObjectNode) coverage.get("subjects")).size());
        result.data.put("drift_findings", driftFindings);
        result.data.put("assertions", evaluated.size());
        ObjectNode summaryNode = result.data.putObject("summary");
        summaryNode.put("verdict", summary.verdict());
        ObjectNode countsNode = summaryNode.putObject("counts");
        for (Map.Entry<String, Integer> e : summary.counts().entrySet()) {
            countsNode.put(e.getKey(), e.getValue());
        }
        ArrayNode topGapsArr = summaryNode.putArray("top_gaps");
        for (Verdict.Gap gap : summary.topGaps()) {
            ObjectNode gapNode = topGapsArr.addObject();
            gapNode.put("outcome", gap.outcome());
            ArrayNode controlsArr = gapNode.putArray("controls");
            gap.controls().forEach(controlsArr::add);
            gapNode.put("more", gap.more());
        }
        result.data.set("activity", activity);
        result.data.set("blind_spots", blindSpots);
        if (options.state() != null) {
            ArrayNode supersedesArr = result.data.putArray("supersedes");
            supersedes.forEach(supersedesArr::add);
        }
        if (nonConformant > 0) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        // SPEC.md:1076 (SPEC Sec.8.5): exit 2 whenever any assertion is both insufficient_evidence and
        // severity: high. Java's assess has no records-folder entrypoint (Python-only, 18.30
        // Dispositions), so this check applies unconditionally, unlike Python's `scanned is None`
        // scoping.
        TreeSet<String> highInsufficient = new TreeSet<>();
        for (Assertions.Assertion a : evaluated) {
            if ("insufficient_evidence".equals(a.outcome) && "high".equals(a.severity)) {
                highInsufficient.add(a.control);
            }
        }
        if (!highInsufficient.isEmpty()) {
            result.addCode(ExitCode.INSUFFICIENT_EVIDENCE.code);
        }
        if (evaluatedNothing(evaluated)) {
            throw nothingEvaluated(profileObj, ingested.accepted, evaluated.size());
        }
        for (String line : Report.activityCliLines(activity, Messages.catalogue(Messages.DEFAULT_LANGUAGE))) {
            result.note(line);
        }
        for (String line : Report.blindSpotsCliLines(blindSpots)) {
            result.note(line);
        }
        result.note("verdict: " + summary.verdict());
        result.note("assessed " + evaluated.size() + " (control, subject) pairs; " + nonConformant + " non-conformant");
        if (!highInsufficient.isEmpty()) {
            result.note("insufficient evidence on severity-high control(s): " + String.join(", ", highInsufficient));
        }
        return result;
    }

    private static CommandResult cmdAssess(String[] args) {
        String outArg = flagValue(args, "out");
        return runAssess(new AssessOptions(
                flagValue(args, "bundle"),
                flagValue(args, "profile"),
                flagValue(args, "catalog"),
                flagValue(args, "domain"),
                flagValues(args, "catalog-dir"),
                outArg != null ? outArg : DEFAULT_OUT_DIR,
                flagValue(args, "state"),
                "assess"));
    }

    /** Assess the bundled quickstart project end to end — one command, offline (SPEC §13.4 AX-1). */
    private static CommandResult cmdQuickstart(String[] args) {
        CommandResult result = new CommandResult("quickstart");
        String outArg = flagValue(args, "out");
        String out = outArg != null ? outArg : DEFAULT_OUT_DIR;
        Path quickstart = Bundled.quickstartDir();
        if (!Files.isDirectory(quickstart)) {
            throw new InputError(
                    "input.quickstart_missing",
                    "the quickstart project is missing at " + quickstart + ".",
                    "reinstall the engine: the quickstart project ships inside the package.");
        }
        Path catalogDir = Bundled.catalogsDir().resolve("base").resolve("eu-ai-act");
        CommandResult assess = runAssess(new AssessOptions(
                quickstart.resolve("evidence").toString(),
                quickstart.resolve("applicability.yaml").toString(),
                "eu-ai-act@2026.09",
                quickstart.resolve("domain.linkml.yaml").toString(),
                List.of(catalogDir.toString()),
                out,
                null,
                "quickstart"));
        result.data.setAll(assess.data);
        result.data.put("quickstart", "ok");
        for (int code : assess.applicableCodes()) {
            if (code != ExitCode.OK.code) {
                result.addCode(code);
            }
        }
        for (String line : assess.humanLines) {
            result.note(line);
        }
        int assertionsCount = assess.data.has("assertions") ? assess.data.get("assertions").asInt() : 0;
        result.note("quickstart complete: " + assertionsCount + " assertions; report in " + out);
        return result;
    }

    /** Re-render a report from a committed {@code assertions.json} (SPEC §9.4), or, with {@code
     * --validate}, schema-validate every artifact in a report directory at parity with the Python
     * reference (item 18.27); the {@code public} format is not yet ported and is refused with a
     * named, honest error rather than a silent guess. */
    private static CommandResult cmdReport(String[] args) {
        CommandResult result = new CommandResult("report");
        if (Arrays.asList(args).contains("--validate")) {
            String reportDir = requireDir(flagValue(args, "validate"), "validate", "the report directory");
            List<String> problems = ReportValidate.validateReport(Paths.get(reportDir));
            result.data.put("report_dir", reportDir);
            result.data.put("valid", problems.isEmpty());
            ArrayNode problemsNode = Json.nodes().arrayNode();
            for (String problem : problems) {
                problemsNode.add(problem);
            }
            result.data.set("problems", problemsNode);
            if (!problems.isEmpty()) {
                result.addCode(ExitCode.INPUT_ERROR.code);
                result.note(reportDir + ": " + problems.size() + " artifact(s) failed validation");
            } else {
                result.note(reportDir + ": all artifacts valid");
            }
            return result;
        }
        String source = requireFile(flagValue(args, "from"), "from", "the assertions file");
        String formatArg = flagValue(args, "format");
        String format = formatArg != null ? formatArg : "md";
        if (!REPORT_FORMATS.contains(format)) {
            throw new InputError(
                    "input.report_format",
                    "unknown or not-yet-implemented report format '" + format + "'.",
                    "choose one of: " + String.join(", ", REPORT_FORMATS) + ".");
        }
        JsonNode parsed = Json.parseFile(Paths.get(source));
        List<Assertions.Assertion> assertions = new ArrayList<>();
        if (parsed != null && parsed.isArray()) {
            for (JsonNode node : parsed) {
                assertions.add(Assertions.fromJson(node));
            }
        }
        Map<String, Integer> counts = Assertions.aggregate(assertions);
        String rendering;
        switch (format) {
            case "md" -> rendering = Report.renderReportMd(assertions, counts, Messages.DEFAULT_LANGUAGE, null, null);
            case "html" -> rendering = Report.renderReportHtml(assertions, counts, Messages.DEFAULT_LANGUAGE, null, null);
            case "oscal" -> rendering = Json.pretty(Report.renderOscal(assertions)) + "\n";
            case "sarif" -> rendering = Json.pretty(Report.renderSarif(assertions)) + "\n";
            default -> {
                Map<String, List<Assertions.Assertion>> bySubject = new LinkedHashMap<>();
                for (Assertions.Assertion a : assertions) {
                    bySubject.computeIfAbsent(a.subject, k -> new ArrayList<>()).add(a);
                }
                List<String> subjects = new ArrayList<>(bySubject.keySet());
                subjects.sort(Json::byteCompare);
                ObjectNode packs = Json.nodes().objectNode();
                for (String subject : subjects) {
                    packs.set(subject, Report.renderEvidencePack(subject, bySubject.get(subject), null));
                }
                rendering = Json.pretty(packs) + "\n";
            }
        }
        result.data.put("from", source);
        result.data.put("format", format);
        result.data.put("rendering", rendering);
        String out = flagValue(args, "out");
        if (out != null) {
            try {
                Files.write(Paths.get(out), rendering.getBytes(StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new IllegalStateException("cannot write " + out + ": " + e.getMessage(), e);
            }
            result.data.put("out", out);
        }
        result.note(rendering);
        return result;
    }

    private static ObjectNode changeToJson(Diff.Change change) {
        // ObjectNode.put(String, String) already writes a NullNode when the value is null.
        ObjectNode node = Json.nodes().objectNode();
        node.put("control", change.control());
        node.put("subject", change.subject());
        node.put("from", change.from());
        node.put("to", change.to());
        return node;
    }

    /** {@code agentce diff} (SPEC §9.3, item 18.6): a real, deterministic assertion-set diff, matching
     * the Python reference's {@code cmd_diff} (see {@link Diff}). */
    private static CommandResult cmdDiff(String[] args) {
        CommandResult result = new CommandResult("diff");
        List<String> positional = positionalArgs(args);
        if (positional.size() > 2) {
            throw new InputError(
                    "input.diff_extra_argument",
                    "diff takes exactly two positional arguments, got " + positional.size() + ".",
                    "pass exactly two files: `agentce diff <report-a> <report-b>`.");
        }
        String fix = "pass two assertion files: `agentce diff <report-a> <report-b>`.";
        String reportA = requireFile(positional.size() > 0 ? positional.get(0) : null, "report_a", "the first assertion set", fix);
        String reportB = requireFile(positional.size() > 1 ? positional.get(1) : null, "report_b", "the second assertion set", fix);
        String format = flagValue(args, "format");
        if (format == null) {
            format = "text";
        }
        if (!Diff.DIFF_FORMATS.contains(format)) {
            throw new InputError(
                    "input.diff_format",
                    "--format must be one of " + String.join(", ", Diff.DIFF_FORMATS) + ", not '" + format + "'.",
                    "pass --format text|json|md.");
        }
        result.data.put("report_a", Diff.normalizePosixPath(reportA));
        result.data.put("report_b", Diff.normalizePosixPath(reportB));
        JsonNode aNode = Json.parseFile(Paths.get(reportA));
        JsonNode bNode = Json.parseFile(Paths.get(reportB));
        List<Diff.Change> changes = Diff.diffAssertionSets((ArrayNode) aNode, (ArrayNode) bNode);
        Map<String, List<Diff.Change>> grouped = Diff.whatChanged(changes);
        result.data.put("changed", changes.size());
        ArrayNode diffArr = result.data.putArray("diff");
        for (Diff.Change change : changes) {
            diffArr.add(changeToJson(change));
        }
        ObjectNode whatChangedNode = result.data.putObject("what_changed");
        for (String key : List.of("closed", "opened", "other")) {
            ArrayNode groupArr = whatChangedNode.putArray(key);
            for (Diff.Change change : grouped.get(key)) {
                groupArr.add(changeToJson(change));
            }
        }
        if (!changes.isEmpty()) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        if (Arrays.asList(args).contains("--json")) {
            // Never render a format only --json will discard -- mirrors Python's early return before
            // any result.note call.
            return result;
        }
        if ("md".equals(format)) {
            for (String line : Diff.diffMdLines(grouped)) {
                result.note(line);
            }
        } else if ("json".equals(format)) {
            result.note(Json.pretty(result.data));
        } else if (!changes.isEmpty()) {
            result.note(changes.size() + " assertion(s) differ:");
            for (Diff.Change change : changes) {
                result.note("  " + Diff.diffChangeLine(change));
            }
        } else {
            result.note("no differences");
        }
        return result;
    }

    /** {@code agentce readiness}'s parsed args (see {@link #parseReadinessArgs}). */
    private record ReadinessArgs(String reportDir, String gaps, String deviations, List<String> catalogDirs) {}

    private static final Pattern NEGATIVE_NUMBER = Pattern.compile("-\\d+|-\\d*\\.\\d+");

    /** A token argparse classifies as an option rather than a value: it starts with {@code -}, is not
     * a bare {@code -}, does not look like a negative number and holds no space
     * ({@code argparse._parse_optional}). */
    private static boolean looksLikeOption(String token) {
        return token.startsWith("-")
                && !"-".equals(token)
                && !NEGATIVE_NUMBER.matcher(token).matches()
                && !token.contains(" ");
    }

    private static InputError readinessUnrecognized(String detail, String fix) {
        return new InputError("input.readiness_unrecognized_flag", detail, fix);
    }

    /** {@code agentce readiness}'s args, read the way Python's {@code readiness} subparser (argparse,
     * {@code allow_abbrev=False}) reads them: {@code --gaps}/{@code --deviations} take one value
     * (separate or {@code =}-joined, the last one wins), {@code --catalog-dir} appends, {@code --json}/
     * {@code --debug}/{@code --quiet} take none, {@code --} ends the options, and there is at most one
     * positional. Anything argparse would refuse (an unknown or abbreviated flag, a value flag with no
     * value, a second positional) throws {@code input.readiness_unrecognized_flag} rather than being
     * skipped, so a mistyped flag can never give a verdict computed without it. An empty
     * {@code --gaps}/{@code --deviations} value is ignored, as Python's {@code if gaps_path:} does. */
    private static ReadinessArgs parseReadinessArgs(String[] args) {
        String flagFix = "pass --gaps, --deviations, or --catalog-dir, or drop the flag.";
        String reportDir = null;
        String gaps = null;
        String deviations = null;
        List<String> catalogDirs = new ArrayList<>();
        boolean optionsEnded = false;
        for (int i = 1; i < args.length; i++) {
            String token = args[i];
            if (!optionsEnded && "--".equals(token)) {
                optionsEnded = true;
                continue;
            }
            if (!optionsEnded && looksLikeOption(token)) {
                int eq = token.indexOf('=');
                String name = eq >= 0 ? token.substring(0, eq) : token;
                if (GLOBAL_BOOLEAN_FLAGS.contains(name)) {
                    if (eq >= 0) {
                        throw readinessUnrecognized("flag '" + name + "' takes no value.", "drop the value: " + name + ".");
                    }
                    continue;
                }
                if (!"--gaps".equals(name) && !"--deviations".equals(name) && !"--catalog-dir".equals(name)) {
                    throw readinessUnrecognized("unrecognized flag '" + token + "'.", flagFix);
                }
                String value;
                if (eq >= 0) {
                    value = token.substring(eq + 1);
                } else {
                    if (i + 1 >= args.length || looksLikeOption(args[i + 1])) {
                        throw readinessUnrecognized("flag '" + name + "' needs a value.", "pass " + name + " <path>.");
                    }
                    value = args[++i];
                }
                if ("--catalog-dir".equals(name)) {
                    catalogDirs.add(value);
                } else if ("--gaps".equals(name)) {
                    gaps = value.isEmpty() ? null : value;
                } else {
                    deviations = value.isEmpty() ? null : value;
                }
                continue;
            }
            if (reportDir != null) {
                throw readinessUnrecognized(
                        "unrecognized argument '" + token + "'.",
                        "pass exactly one report directory: `agentce readiness <report-dir>`.");
            }
            reportDir = token;
        }
        return new ReadinessArgs(reportDir, gaps, deviations, catalogDirs);
    }

    /** Every control's severity, from the given {@code --catalog-dir}s, else every vendored base
     * catalog (SPEC §13.3.4). Deliberately <b>not</b> a factoring of {@link #vendoredCatalogs}: that
     * method silently skips a subdirectory whose {@code catalog.yaml} fails to parse and also scans
     * {@code overlays/}, neither of which matches this method's own throw-on-malformed, {@code
     * base}-only behaviour (mirrors Python's {@code _readiness_severities}, which has no try/catch
     * around {@code load_catalog}). A later catalog's control silently overwrites an earlier one
     * sharing an id (plain last-write-wins, matching Python's dict-comprehension). */
    private static Map<String, String> readinessSeverities(List<String> catalogDirs) {
        List<String> dirs = catalogDirs;
        if (dirs.isEmpty()) {
            Path base = Bundled.catalogsDir().resolve("base");
            List<String> names = new ArrayList<>();
            if (Files.isDirectory(base)) {
                try (Stream<Path> stream = Files.list(base)) {
                    stream.map(p -> p.getFileName().toString()).forEach(names::add);
                } catch (IOException ignored) {
                    // no vendored base catalogs
                }
            }
            names.sort(Json::byteCompare);
            dirs = new ArrayList<>();
            for (String name : names) {
                Path dir = base.resolve(name);
                if (Files.isRegularFile(dir.resolve("catalog.yaml"))) {
                    dirs.add(dir.toString());
                }
            }
        }
        Map<String, String> severities = new LinkedHashMap<>();
        for (String dir : dirs) {
            Catalog catalog = Catalog.load(Paths.get(requireDir(dir, "catalog-dir", "a catalog directory")));
            for (Catalog.ControlSpec control : catalog.controls) {
                severities.put(control.id, control.severity);
            }
        }
        return severities;
    }

    /** {@code agentce readiness} (SPEC §13.3.4 stage 4, item 18.25): the report-readiness verdict,
     * matching the Python reference's {@code cmd_readiness} byte for byte (see {@code Readiness.java}).
     * Exit 0 for READY and READY WITH LIMITATIONS, 1 for NOT READY -- the verdict logic lives in the
     * engine, never in a skill. */
    private static CommandResult cmdReadiness(String[] args) {
        CommandResult result = new CommandResult("readiness");
        ReadinessArgs parsed = parseReadinessArgs(args);
        Path reportDir = Paths.get(requireDir(
                parsed.reportDir(),
                "report_dir",
                "the report directory",
                "pass the report directory: `agentce readiness <report-dir>`."));
        Set<String> gaps = new LinkedHashSet<>();
        String gapsPath = parsed.gaps();
        if (gapsPath != null) {
            String text;
            try {
                text = Files.readString(Paths.get(requireFile(gapsPath, "gaps", "the gaps file")), StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + gapsPath + ": " + e.getMessage(), e);
            }
            gaps = Readiness.parseGapsFile(text);
        }
        List<JsonNode> deviations = List.of();
        String deviationsPath = parsed.deviations();
        if (deviationsPath != null) {
            deviations = Readiness.loadDeviationRegister(
                    Paths.get(requireFile(deviationsPath, "deviations", "the deviation register")));
        }
        Map<String, String> severities = readinessSeverities(parsed.catalogDirs());
        Readiness.Verdict verdict = Readiness.computeReadiness(reportDir, severities, deviations, gaps);
        result.data.put("verdict", verdict.verdict());
        ArrayNode reasonsArr = result.data.putArray("reasons");
        verdict.reasons().forEach(reasonsArr::add);
        ArrayNode limitationsArr = result.data.putArray("limitations");
        verdict.limitations().forEach(limitationsArr::add);
        Path out = reportDir.resolve("report-readiness-" + java.time.LocalDate.now() + ".md").normalize();
        List<String> lines = new ArrayList<>();
        lines.add("# Report readiness — " + verdict.verdict());
        lines.add("");
        if (!verdict.reasons().isEmpty()) {
            lines.add("## Blocking reasons");
            for (String r : verdict.reasons()) {
                lines.add("- " + r);
            }
            lines.add("");
        }
        if (!verdict.limitations().isEmpty()) {
            lines.add("## Limitations");
            for (String l : verdict.limitations()) {
                lines.add("- " + l);
            }
            lines.add("");
        }
        try {
            Files.writeString(out, String.join("\n", lines) + "\n");
        } catch (IOException e) {
            throw new IllegalStateException("cannot write " + out + ": " + e.getMessage(), e);
        }
        result.data.put("report", out.toString());
        if (Readiness.NOT_READY.equals(verdict.verdict())) {
            result.addCode(ExitCode.FINDINGS.code);
        }
        result.note(verdict.verdict() + ": " + verdict.reasons().size() + " reason(s), "
                + verdict.limitations().size() + " limitation(s)");
        return result;
    }

    private static final List<String> SIGN_PROFILES = List.of("sigstore-public", "sigstore-private", "kms");

    /** {@code agentce sign}'s parsed args (see {@link #parseSignArgs}). */
    private record SignArgs(
            String reportDir, String asRole, String profile, String key, boolean dryRun, boolean writeTrustRoot) {}

    private static InputError signUnrecognized(String detail, String fix) {
        return new InputError("input.sign_unrecognized_flag", detail, fix);
    }

    /** {@code sign}'s own no-value flags, checked alongside {@link #GLOBAL_BOOLEAN_FLAGS} so
     * {@link #parseSignArgs} rejects a value on either group with one check. */
    private static final Set<String> SIGN_BOOLEAN_FLAGS = Set.of("--dry-run", "--write-trust-root");

    /** {@code agentce sign}'s args, read the way Python's {@code sign} subparser (argparse,
     * {@code allow_abbrev=False}) reads them: {@code --as}/{@code --profile}/{@code --key} take one
     * value (separate or {@code =}-joined, the last one wins), {@code --dry-run}/
     * {@code --write-trust-root} take none, {@code --} ends the options, and there is at most one
     * positional -- the same shape {@link #parseReadinessArgs} established for {@code readiness} in
     * 18.25. Anything argparse would refuse throws {@code input.sign_unrecognized_flag} rather than
     * being silently skipped. */
    private static SignArgs parseSignArgs(String[] args) {
        String flagFix = "pass --as, --profile, --key, --dry-run, or --write-trust-root, or drop the flag.";
        String reportDir = null;
        String asRole = null;
        String profile = null;
        String key = null;
        boolean dryRun = false;
        boolean writeTrustRoot = false;
        boolean optionsEnded = false;
        for (int i = 1; i < args.length; i++) {
            String token = args[i];
            if (!optionsEnded && "--".equals(token)) {
                optionsEnded = true;
                continue;
            }
            if (!optionsEnded && looksLikeOption(token)) {
                int eq = token.indexOf('=');
                String name = eq >= 0 ? token.substring(0, eq) : token;
                if (GLOBAL_BOOLEAN_FLAGS.contains(name) || SIGN_BOOLEAN_FLAGS.contains(name)) {
                    if (eq >= 0) {
                        throw signUnrecognized("flag '" + name + "' takes no value.", "drop the value: " + name + ".");
                    }
                    if ("--dry-run".equals(name)) {
                        dryRun = true;
                    } else if ("--write-trust-root".equals(name)) {
                        writeTrustRoot = true;
                    }
                    continue;
                }
                if (!"--as".equals(name) && !"--profile".equals(name) && !"--key".equals(name)) {
                    throw signUnrecognized("unrecognized flag '" + token + "'.", flagFix);
                }
                String value;
                if (eq >= 0) {
                    value = token.substring(eq + 1);
                } else {
                    if (i + 1 >= args.length || looksLikeOption(args[i + 1])) {
                        throw signUnrecognized("flag '" + name + "' needs a value.", "pass " + name + " <value>.");
                    }
                    value = args[++i];
                }
                if ("--as".equals(name)) {
                    asRole = value;
                } else if ("--profile".equals(name)) {
                    profile = value;
                } else {
                    key = value;
                }
                continue;
            }
            if (reportDir != null) {
                throw signUnrecognized(
                        "unrecognized argument '" + token + "'.",
                        "pass exactly one report directory: `agentce sign <report-dir> --as claimant|assessor`.");
            }
            reportDir = token;
        }
        return new SignArgs(reportDir, asRole, profile, key, dryRun, writeTrustRoot);
    }

    /** Python's {@code dict.get(key, default)} restricted to the one shape this call site needs:
     * {@code obj} may be any JSON value (a hostile {@code claim.json} need not carry a mapping at every
     * level), and {@code default} is substituted only when {@code obj} is itself a plain object missing
     * {@code key}, never when {@code obj} is some other type -- matching
     * {@code claim.get("claimant", {}).get("org", "unset")}'s own attribute-style access only making
     * sense on an actual mapping. */
    private static JsonNode pyGetField(JsonNode obj, String key, JsonNode defaultValue) {
        if (obj != null && obj.isObject() && obj.has(key)) {
            return obj.get(key);
        }
        return defaultValue;
    }

    /** Resolves the operator's signing key for {@code agentce sign} (SPEC §9.1), matching
     * {@code _sign_signer} exactly: {@code kms} signs with an operator-held {@code --key}; the two
     * keyless {@code sigstore-*} profiles always refuse offline (this port never obtains a Fulcio
     * certificate). */
    private static Sign.Signer signSigner(String keyPath, String profile) {
        if ("kms".equals(profile)) {
            if (keyPath == null || keyPath.isEmpty()) {
                throw new InputError(
                        "sign.kms_key_missing",
                        "the kms profile signs with an operator-held key.",
                        "pass --key <ed25519-private-key.pem>.");
            }
            return Sign.KmsSigner.load(Paths.get(requireFile(keyPath, "key", "the signing key")));
        }
        throw new InputError(
                "sign.keyless_offline",
                "the " + profile + " profile is keyless and obtains a certificate from a Fulcio instance "
                        + "(network); the engine does not sign it offline.",
                "use --profile kms --key <file> offline, or run keyless signing where the Fulcio and "
                        + "Rekor endpoints are reachable.");
    }

    /** {@code agentce sign} (SPEC §8.7, §9.1, item 18.26): the Ed25519/DSSE/in-toto signing flow,
     * {@code kms} profile, matching the Python reference's {@code cmd_sign} byte for byte (see
     * {@code Sign.java}). Refuses to sign unless the report's readiness verdict is not
     * {@code NOT READY} ({@code agentce readiness}'s own gate, computed the same way). */
    private static CommandResult cmdSign(String[] args) {
        CommandResult result = new CommandResult("sign");
        SignArgs parsed = parseSignArgs(args);
        Path reportDir = Paths.get(requireDir(
                parsed.reportDir(),
                "report_dir",
                "the report directory",
                "pass the report directory: `agentce sign <report-dir> --as claimant|assessor`."));
        String role = parsed.asRole();
        if (!"claimant".equals(role) && !"assessor".equals(role)) {
            throw new InputError(
                    "input.sign_role", "--as must be `claimant` or `assessor`.", "pass --as claimant|assessor.");
        }
        String profile = parsed.profile() == null || parsed.profile().isEmpty() ? "sigstore-public" : parsed.profile();
        if (!SIGN_PROFILES.contains(profile)) {
            throw new InputError(
                    "input.sign_profile",
                    "unknown signing profile " + Readiness.pyRepr(Json.nodes().textNode(profile)) + ".",
                    "choose one of: " + String.join(", ", SIGN_PROFILES) + ".");
        }
        boolean writeTrustRoot = parsed.writeTrustRoot();
        if (writeTrustRoot && !"kms".equals(profile)) {
            throw new InputError(
                    "sign.trust_root_requires_kms",
                    "--write-trust-root needs an exportable public key; the "
                            + Readiness.pyRepr(Json.nodes().textNode(profile)) + " profile has none.",
                    "pass --profile kms --key <ed25519-private-key.pem> --write-trust-root.");
        }
        boolean dryRun = parsed.dryRun();
        result.data.put("report_dir", reportDir.toString());
        result.data.put("as", role);
        result.data.put("profile", profile);
        result.data.put("dry_run", dryRun);

        // The engine refuses to sign a report that is not ready to publish (SPEC §8.5); `sign` never
        // takes its own `--catalog-dir`/`--gaps`/`--deviations` flags, so this always uses the bundled
        // catalogs with no deviations/gaps.
        Readiness.Verdict verdict =
                Readiness.computeReadiness(reportDir, readinessSeverities(List.of()), List.of(), Set.of());
        result.data.put("readiness", verdict.verdict());
        if (Readiness.NOT_READY.equals(verdict.verdict())) {
            throw new InputError(
                    "sign.not_ready",
                    "the report is " + verdict.verdict() + ": " + String.join("; ", verdict.reasons()) + ".",
                    "resolve the blocking reasons (agentce readiness <report-dir>) before signing.");
        }

        if (dryRun) {
            result.note("dry run: would sign the claim as " + role + " (" + profile + ")");
            return result;
        }

        Path claimPath = reportDir.resolve("claim.json");
        if (!Files.isRegularFile(claimPath)) {
            throw new InputError(
                    "sign.no_claim",
                    "the report directory has no claim.json to sign.",
                    "produce the report first: `agentce assess … --out <report-dir>`.");
        }
        ObjectNode claim = (ObjectNode) Json.parseFile(claimPath);

        Sign.Signer signer = signSigner(parsed.key(), profile);
        ArrayNode subjects = Sign.signSubjects(reportDir, claim);
        ObjectNode statement = Json.nodes().objectNode();
        statement.put("_type", Sign.INTOTO_STATEMENT_TYPE);
        statement.set("subject", subjects);
        statement.put("predicateType", "https://agent-conformance.org/attestation/claim/v1");
        ObjectNode predicate = statement.putObject("predicate");
        predicate.put("role", role);
        predicate.put("profile", profile);
        predicate.put(
                "statement",
                "This report states conformance to the named catalogs as evaluated by the named engine "
                        + "over the named evidence. It is not a legal compliance determination.");
        ObjectNode envelope = Sign.signStatement(statement, signer);
        ObjectNode record = Json.nodes().objectNode();
        record.put("role", role);
        record.put("profile", profile);
        record.setAll(envelope);
        // A present-but-non-array "signatures" (null, a string, an object) matches Python's
        // `claim["signatures"].append(record)` crashing with AttributeError, not a silent
        // replacement with a fresh array (18.26 round-3 verifier finding).
        JsonNode existingSignatures = claim.get("signatures");
        if (existingSignatures != null && !existingSignatures.isArray()) {
            throw new IllegalArgumentException(
                    "claim.json's \"signatures\" is not an array (got " + existingSignatures.getNodeType() + ")");
        }
        ArrayNode signatures = existingSignatures != null
                ? (ArrayNode) existingSignatures
                : claim.putArray("signatures");
        signatures.add(record);
        claim.set("signatures", signatures);
        try {
            Files.writeString(claimPath, Json.pretty(claim) + "\n");
        } catch (IOException e) {
            throw new IllegalStateException("cannot write " + claimPath + ": " + e.getMessage(), e);
        }
        Path sigDir = reportDir.resolve("signatures");
        try {
            Files.createDirectories(sigDir);
            Path detached = sigDir.resolve(role + "-" + profile + ".dsse.json");
            Files.writeString(detached, Json.pretty(record) + "\n");
            result.data.put("signature", detached.toString());
        } catch (IOException e) {
            throw new IllegalStateException("cannot write to " + sigDir + ": " + e.getMessage(), e);
        }
        result.data.put("keyid", signer.keyid());
        result.data.put("signatures", signatures.size());

        if (writeTrustRoot) {
            // A KmsSigner is the only signer reachable here: `writeTrustRoot` requires `profile ==
            // "kms"` (checked above), and `signSigner` only ever returns a KmsSigner for that profile
            // -- asserted at runtime, matching Python's own `assert isinstance(signer, KmsSigner)`.
            if (!(signer instanceof Sign.KmsSigner kmsSigner)) {
                throw new IllegalStateException("internal: --write-trust-root reached with a non-KmsSigner");
            }
            JsonNode claimant = pyGetField(claim, "claimant", Json.nodes().objectNode());
            JsonNode org = pyGetField(claimant, "org", Json.nodes().textNode("unset"));
            Path trustRootPath = reportDir.resolve("trust-root.json");
            ObjectNode document = Json.nodes().objectNode();
            ObjectNode keys = document.putObject("keys");
            ObjectNode keyEntry = keys.putObject(kmsSigner.keyid());
            keyEntry.put("public_key", kmsSigner.publicKeyB64());
            keyEntry.set("identity", org);
            try {
                Files.writeString(trustRootPath, Json.pretty(document) + "\n");
            } catch (IOException e) {
                throw new IllegalStateException("cannot write " + trustRootPath + ": " + e.getMessage(), e);
            }
            result.data.put("trust_root", trustRootPath.toString());
        }
        result.note("signed " + claimPath.getFileName() + " as " + role + " (" + profile + ")");
        return result;
    }

    /** {@code agentce verify --bundle/--catalog/--release/--report}: offline DSSE/certificate
     * verification (SPEC §8.7, §9.1). Ports {@code cmd_verify}'s own input validation verbatim
     * ({@code commands/__init__.py:254-282}); {@code --report} (the 9-stage offline reproduction) is
     * out of scope for this port (18.26's own disposition) and falls to {@link #notImplemented} once
     * validation passes. Mirrors {@code cli.ts}'s {@code cmdVerify} exactly. */
    private static CommandResult cmdVerify(String[] args) {
        CommandResult result = new CommandResult("verify");
        // An empty value (`--catalog ""`) is treated as not provided, matching the Python
        // reference's `if value` truthiness check (`commands/__init__.py:261-269`): an empty string
        // must never fall through to `requireDir`/`Files.exists`, where `Paths.get("")` resolves to
        // the current working directory and a catalog or release check could wrongly verify it (F4,
        // 18.65).
        VerifyArgs parsed = parseVerifyArgs(args);
        String bundle = verifyTarget(parsed, "bundle");
        String catalog = verifyTarget(parsed, "catalog");
        String release = verifyTarget(parsed, "release");
        String report = verifyTarget(parsed, "report");
        int chosenCount = (bundle != null ? 1 : 0) + (catalog != null ? 1 : 0)
                + (release != null ? 1 : 0) + (report != null ? 1 : 0);
        if (chosenCount != 1) {
            throw new InputError(
                    "input.verify_target",
                    "verify needs exactly one of --bundle, --catalog, --release, or --report.",
                    "pass exactly one target, e.g. `agentce verify --bundle <dir>`.");
        }
        String signerTrustRoot = parsed.signerTrustRoot();
        String expectKeyid = parsed.expectKeyid();
        if (report == null && (signerTrustRoot != null || expectKeyid != null)) {
            throw new InputError(
                    "input.verify_target",
                    "--signer-trust-root/--expect-keyid apply only to --report.",
                    "pass --report <dir> together with --signer-trust-root/--expect-keyid, or drop them.");
        }

        if (bundle != null) {
            String bundleDir = requireDir(bundle, "bundle", "the evidence bundle");
            Bundle loaded = Bundle.load(Paths.get(bundleDir));
            Ingest.Result ingested = Ingest.ingest(loaded);
            List<Integrity.Result> results = Integrity.verifyBundle(ingested.accepted, loaded.manifest, loaded.root);
            Set<String> clean = Set.of("verified", "verified_weak");
            int broken = 0;
            ArrayNode streams = Json.nodes().arrayNode();
            for (Integrity.Result r : results) {
                streams.add(r.toJson());
                if (!clean.contains(r.status)) {
                    broken++;
                }
            }
            result.data.put("bundle", bundleDir);
            result.data.set("streams", streams);
            result.data.put("stream_count", results.size());
            result.data.put("broken_streams", broken);
            result.note("verified " + bundleDir + ": " + results.size() + " streams, " + broken + " broken");
            if (broken > 0) {
                result.addCode(ExitCode.FINDINGS.code);
            }
            return result;
        }

        if (catalog != null) {
            String catalogDir = requireDir(catalog, "catalog", "the catalog directory");
            ObjectNode outcome = Verify.verifyCatalog(Paths.get(catalogDir), Verify.vendoredTrust());
            result.data.put("catalog", catalogDir);
            result.data.setAll(outcome);
            if (outcome.path("verified").asBoolean(false)) {
                result.note("verified catalog " + Paths.get(catalogDir).getFileName() + ": signer "
                        + outcome.path("signer").asText());
            } else {
                result.note("catalog " + Paths.get(catalogDir).getFileName() + ": verification failed");
                result.addCode(ExitCode.INPUT_ERROR.code);
            }
            return result;
        }

        if (release != null) {
            Path releasePath = Paths.get(release);
            // `Paths.get("file.tar/")` drops the trailing slash while building the path, so
            // `Files.exists` alone would say a plain file "exists" even though a trailing slash
            // asserts a directory; a trailing slash on something that is not a directory must refuse,
            // matching the raw-string `os.path.exists` check in the Python reference (F5, 18.65).
            boolean trailingSlashOnNonDirectory =
                    (release.endsWith("/") || release.endsWith(File.separator)) && !Files.isDirectory(releasePath);
            if (!Files.exists(releasePath) || trailingSlashOnNonDirectory) {
                throw new InputError(
                        "input.release_missing",
                        "the release artifact " + Readiness.pyRepr(Json.nodes().textNode(release)) + " does not exist.",
                        "pass --release <bundle-dir-or-envelope>.");
            }
            ObjectNode outcome = Verify.verifyRelease(releasePath, Verify.vendoredTrust());
            result.data.setAll(outcome);
            if (outcome.path("verified").asBoolean(false)) {
                if (outcome.has("signer")) {
                    result.note("verified " + Paths.get(release).getFileName() + ": signer "
                            + outcome.path("signer").asText());
                } else {
                    result.note("verified release " + Paths.get(release).getFileName() + ": "
                            + outcome.path("signers").size() + " signatures");
                }
            } else {
                result.note("release " + Paths.get(release).getFileName() + ": verification failed");
                result.addCode(ExitCode.INPUT_ERROR.code);
            }
            return result;
        }

        // `report` is the only remaining target (exactly-one was already enforced above).
        return notImplemented("verify");
    }

    /** {@code agentce version} (SPEC §8.5): the same structured envelope every other command returns,
     * with a real installed-artifact {@code no_ml} self-report (mirrors Python's {@code cmd_version}
     * and the TypeScript port's {@code cmdVersion}). Distinct from the bare {@code --version}/
     * {@code -V} flag, which stays a plain one-line shortcut (handled before this is ever reached,
     * {@link #run}). */
    private static CommandResult cmdVersion() {
        CommandResult result = new CommandResult("version");
        NoMl.InstalledResult scan = NoMl.evaluateInstalled(NoMl.loadVendoredDenylist(), NoMl.loadRuntimeDeps());
        result.data.put("engine", Version.ENGINE_NAME);
        result.data.put("engine_version", Version.ENGINE_VERSION);
        result.data.put("spec_version", Version.SPEC_VERSION);
        result.data.putArray("supported_catalogs");
        result.data.put("no_ml", scan.result);
        ObjectNode detail = result.data.putObject("no_ml_detail");
        detail.put("result", scan.result);
        ArrayNode present = detail.putArray("denylisted_present");
        scan.denylistedPresent.forEach(present::add);
        result.note(Version.ENGINE_NAME + " " + Version.ENGINE_VERSION + " (spec " + Version.SPEC_VERSION + ")");
        result.note("no_ml: " + scan.result);
        if (!"pass".equals(scan.result)) {
            // A learned component is present: an input/environment error for a model-free engine.
            result.addCode(ExitCode.INPUT_ERROR.code);
        }
        return result;
    }

    /**
     * The {@code otel-genai-fixture} seam's {@code REPORT} line: compact JSON, field order fixed
     * to match TypeScript's {@code JSON.stringify} output byte for byte (18.29, C3).
     */
    private static String otelGenaiReportLine(OtelGenai.AdapterReport report) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("adapter", report.adapter());
        ArrayNode conventions = node.putArray("conventions");
        report.conventions().forEach(conventions::add);
        node.put("spans_seen", report.spansSeen());
        node.put("events_emitted", report.eventsEmitted());
        ArrayNode skipped = node.putArray("skipped");
        for (OtelGenai.SkippedSpan s : report.skipped()) {
            ObjectNode entry = skipped.addObject();
            entry.put("name", s.name());
            entry.put("reason", s.reason());
            entry.put("span_id", s.spanId());
        }
        return Json.compact(node);
    }

    private static CommandResult notImplemented(String command) {
        CommandResult result = new CommandResult(command);
        result.addCode(ExitCode.INPUT_ERROR.code);
        ObjectNode error = result.data.putObject("error");
        error.put("message_key", "cli.not_implemented");
        error.put("detail", "this command is not yet implemented in the Java engine");
        if (command == null || command.isEmpty()) {
            error.putNull("command");
        } else {
            error.put("command", command);
        }
        result.note("agentce (Java engine) — command not yet implemented.");
        return result;
    }

    private static CommandResult errorResult(String command, AgentceError error) {
        CommandResult result = new CommandResult(command);
        result.addCode(error.exitCode);
        ObjectNode err = result.data.putObject("error");
        err.put("message_key", error.key);
        err.put("detail", error.reason);
        err.put("fix", error.fix);
        result.note(error.key + ": " + error.reason);
        if (error.fix != null && !error.fix.isEmpty()) {
            result.note("fix: " + error.fix);
        }
        return result;
    }
}
