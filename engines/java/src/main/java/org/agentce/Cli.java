package org.agentce;

import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Arrays;

/**
 * The {@code agentce} command-line interface (SPEC §8.5): the same {@code --json} envelope and
 * exit-code scheme as the reference. Parity is scoped to the Engine Conformance Suite path; other
 * verbs return a stable {@code input_error} envelope rather than a guess.
 */
public final class Cli {
    private Cli() {}

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        String command = args.length > 0 ? args[0] : null;
        if ("--version".equals(command) || "-V".equals(command) || "version".equals(command)) {
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

        boolean json = Arrays.asList(args).contains("--json");
        CommandResult result;
        try {
            if ("conformance".equals(command)) {
                result = cmdConformance(args);
            } else {
                result = notImplemented(command == null ? "" : command);
            }
        } catch (AgentceError exc) {
            result = errorResult(command == null ? "" : command, exc);
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

    private static String requireDir(String raw, String key, String what) {
        String fix = "pass --" + key + " <dir>.";
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
