package org.agentce;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * The result of running one command, and its deterministic {@code --json} envelope: the command's
 * {@code data} at the top level, the reserved exit-code keys layered on top. This mirrors the Python
 * engine's envelope byte for byte.
 */
public final class CommandResult {
    public final String command;
    private final Set<Integer> codes = new LinkedHashSet<>();
    public final ObjectNode data = Json.nodes().objectNode();
    public final List<String> humanLines = new ArrayList<>();

    public CommandResult(String command) {
        this.command = command;
    }

    public void addCode(int code) {
        ExitCode.nameOf(code); // validates
        codes.add(code);
    }

    public void note(String line) {
        humanLines.add(line);
    }

    public int exitCode() {
        return ExitCode.combine(codes);
    }

    public List<Integer> applicableCodes() {
        return ExitCode.applicable(codes);
    }

    public ObjectNode envelope() {
        ObjectNode env = data.deepCopy();
        env.put("command", command);
        env.put("exit_code", exitCode());
        ArrayNode codesArr = env.putArray("exit_codes");
        ArrayNode statusArr = env.putArray("exit_status");
        for (int c : applicableCodes()) {
            codesArr.add(c);
            statusArr.add(ExitCode.nameOf(c));
        }
        return env;
    }
}
