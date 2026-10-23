package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;

/** The shared report-file-rule table ({@code verification/gates/fixtures/report_file_rule/cases.json},
 * 18.184q), read by Python's and TypeScript's tests too. */
final class ReportFileRuleCases {
    private ReportFileRuleCases() {}

    /** One row: {@code content} is written to {@code file} ({@code null} deletes it), {@code readiness}
     * is the exact reasons list, and {@code validate} is {@code "refused"}, {@code "valid"} or {@code null}. */
    record Case(String id, String file, String content, List<String> readiness, String validate) {}

    static List<Case> all() {
        JsonNode table = Json.parseFile(
                TestPaths.repoRoot().resolve("verification/gates/fixtures/report_file_rule/cases.json"));
        List<Case> cases = new ArrayList<>();
        for (JsonNode row : table.get("cases")) {
            String content = row.get("content").isNull() ? null : row.get("content").textValue();
            JsonNode repeat = row.get("repeat");
            if (content != null && repeat != null) {
                int times = repeat.get("times").intValue();
                content = content.replace(
                        "@@",
                        repeat.get("open").textValue().repeat(times) + repeat.get("close").textValue().repeat(times));
            }
            List<String> readiness = new ArrayList<>();
            row.get("readiness").forEach(reason -> readiness.add(reason.textValue()));
            JsonNode validate = row.get("validate");
            cases.add(new Case(
                    row.get("id").textValue(), row.get("file").textValue(), content, readiness,
                    validate.isNull() ? null : validate.textValue()));
        }
        return cases;
    }
}
