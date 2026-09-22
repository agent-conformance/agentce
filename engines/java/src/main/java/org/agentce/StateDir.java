package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * The incremental-assessment state directory (SPEC §5.4 B7, §9.6, HR-10).
 *
 * <p>A faithful port of the reference {@code state.py}: an index of ingested bundle digests and the
 * identity of the last report written. Re-running over the same bundle digest is a no-op (SPEC §8.2
 * #8); a changed bundle — most importantly late-arriving evidence inside an already-assessed window —
 * supersedes the prior report (HR-10). The directory carries a {@code state_version}; an engine that
 * meets an incompatible version refuses with exit code 3 and names the migration command. Late-event
 * counting is tracked in the reference but has no manifest field yet and is not surfaced on the CLI
 * envelope (the same scope the TypeScript port uses).
 */
public final class StateDir {
    public static final int STATE_VERSION = 1;
    private static final String STATE_FILE = "state.json";

    public final Path path;
    public int version = STATE_VERSION;
    public List<String> bundleDigests = new ArrayList<>();
    public String lastReportDigest;
    public String lastWindowEnd;

    private StateDir(Path path) {
        this.path = path;
    }

    /** The observation window's end: the declared end, else the latest event time, else the epoch. */
    public static String windowEnd(Map<String, String> observationWindow, List<JsonNode> events) {
        if (observationWindow.containsKey("end")) {
            return observationWindow.get("end");
        }
        List<String> times = new ArrayList<>();
        for (JsonNode e : events) {
            JsonNode t = e.get("time");
            if (t != null && t.isTextual() && !t.textValue().isEmpty()) {
                times.add(t.textValue());
            }
        }
        times.sort(Json::byteCompare);
        return times.isEmpty() ? "1970-01-01T00:00:00Z" : times.get(times.size() - 1);
    }

    public static StateDir load(Path path) {
        StateDir state = new StateDir(path);
        Path file = path.resolve(STATE_FILE);
        if (!Files.isRegularFile(file)) {
            return state;
        }
        JsonNode data = Json.parseFile(file);
        int version = data.has("state_version") && data.get("state_version").isNumber()
                ? data.get("state_version").asInt()
                : 0;
        if (version != STATE_VERSION) {
            throw new InputError(
                    "input.state_version_incompatible",
                    "the state directory at " + path + " is state_version " + version
                            + ", but this engine writes state_version " + STATE_VERSION + ".",
                    "run `agentce state migrate --state " + path + "` before re-assessing.");
        }
        state.version = version;
        JsonNode digests = data.get("bundle_digests");
        if (digests != null && digests.isArray()) {
            for (JsonNode d : digests) {
                state.bundleDigests.add(d.asText());
            }
        }
        JsonNode lastReport = data.get("last_report_digest");
        state.lastReportDigest = lastReport != null && lastReport.isTextual() ? lastReport.textValue() : null;
        JsonNode lastEnd = data.get("last_window_end");
        state.lastWindowEnd = lastEnd != null && lastEnd.isTextual() ? lastEnd.textValue() : null;
        return state;
    }

    public void save() {
        try {
            Files.createDirectories(path);
            ObjectNode payload = Json.nodes().objectNode();
            payload.put("state_version", STATE_VERSION);
            ArrayNode digests = payload.putArray("bundle_digests");
            new TreeSet<>(bundleDigests).forEach(digests::add);
            if (lastReportDigest != null) {
                payload.put("last_report_digest", lastReportDigest);
            } else {
                payload.putNull("last_report_digest");
            }
            if (lastWindowEnd != null) {
                payload.put("last_window_end", lastWindowEnd);
            } else {
                payload.putNull("last_window_end");
            }
            Files.write(path.resolve(STATE_FILE), (Json.pretty(payload) + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot write state to " + path + ": " + e.getMessage(), e);
        }
    }

    /** The prior report to supersede for a new assessment against this state (HR-10), or none: firing
     * requires a prior report and a bundle digest this state has not already indexed (idempotent re-run,
     * SPEC §8.2 #8). */
    public List<String> plan(String bundleDigest, List<JsonNode> accepted, String newWindowEnd) {
        if (lastReportDigest == null || bundleDigests.contains(bundleDigest)) {
            return List.of();
        }
        return List.of(lastReportDigest);
    }

    /** Record the assessment: index the bundle digest and remember the new report's manifest digest. */
    public String record(String bundleDigest, Path manifestPath, String newWindowEnd) {
        try {
            String digest = "sha256:" + Canonical.sha256Hex(Files.readAllBytes(manifestPath));
            if (!bundleDigests.contains(bundleDigest)) {
                bundleDigests.add(bundleDigest);
            }
            lastReportDigest = digest;
            lastWindowEnd = newWindowEnd;
            save();
            return digest;
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + manifestPath + ": " + e.getMessage(), e);
        }
    }
}
