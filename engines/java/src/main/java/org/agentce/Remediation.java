package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * The AI-actionable remediation package and its Markdown prompt (SPEC §7; Appendix A2 (A) and (B)).
 *
 * <p>A faithful port of the Python reference's {@code render_remediation_package} and
 * {@code render_remediation_md} ({@code report.py}), as the TypeScript engine's {@code remediation.ts}
 * does: a pure, deterministic join of each non-passing control's catalog data with that control's real
 * assertion outcome, and the one language-neutral template ({@code spec/report/templates/remediation.md.tmpl},
 * vendored under {@code resources/templates/}) rendered over a sanitised copy of the package. Catalog
 * values are read with Python's {@code str()} and truthiness, so every engine writes the same bytes.
 */
final class Remediation {
    private Remediation() {}

    /** Outcomes a package reports as a finding; {@code not_assessed} gets its own array. */
    private static final Set<String> FINDING_OUTCOMES = Set.of("non-conformant", "partial", "insufficient_evidence");
    /** The fixed guardrail block every finding cites by reference. */
    private static final String GUARDRAILS_REF = "remediation.guardrails.v1";
    private static final Set<String> SEVERITY_LEVELS = Set.of("high", "medium", "low");
    private static final String SCHEMA_URL = "https://agent-conformance.org/spec/report/remediation-package.schema.json";
    /** The remediation prompt template's text (Python's {@code bundled.remediation_template()}). */
    static String remediationTemplate() {
        return Bundled.template("remediation.md.tmpl");
    }

    /** Python's {@code str(d.get(key, fallback))}: an absent key gives {@code fallback}, a present one its {@code str()}. */
    private static String strGet(JsonNode d, String key, String fallback) {
        return d != null && d.isObject() && d.has(key) ? Readiness.pyStr(d.get(key)) : fallback;
    }

    private static String strGet(JsonNode d, String key) {
        return strGet(d, key, "");
    }

    private static JsonNode rawField(Catalog.ControlSpec spec, String key) {
        return spec != null && spec.raw != null ? spec.raw.get(key) : null;
    }

    /** Python's {@code spec.raw.get(key, []) or []} over a catalog list field. */
    private static List<JsonNode> rawList(Catalog.ControlSpec spec, String key) {
        return asList(rawField(spec, key));
    }

    private static String severity(Catalog.ControlSpec spec) {
        return spec != null && spec.severity != null && SEVERITY_LEVELS.contains(spec.severity)
                ? spec.severity
                : "unrated";
    }

    private static String findingTitle(Assertions.Assertion a, Catalog.ControlSpec spec) {
        return spec != null && spec.title != null && !spec.title.isEmpty() ? spec.title : a.control;
    }

    /** Every crosswalk clause as the catalog carries it; only a JSON/YAML {@code true} counts as verified. */
    private static ArrayNode clauses(Catalog.ControlSpec spec) {
        ArrayNode out = Json.nodes().arrayNode();
        for (JsonNode entry : rawList(spec, "crosswalk")) {
            JsonNode verified = entry.get("verified_against_text");
            out.addObject()
                    .put("framework", strGet(entry, "framework"))
                    .put("clause", strGet(entry, "clause"))
                    .put("relation", strGet(entry, "relation"))
                    .put("verified_against_text", verified != null && verified.isBoolean() && verified.booleanValue());
        }
        return out;
    }

    private static ArrayNode expectations(Catalog.ControlSpec spec, String outcome) {
        ArrayNode out = Json.nodes().arrayNode();
        for (JsonNode e : rawList(spec, "expectations")) {
            out.addObject().put("id", strGet(e, "id")).put("text", strGet(e, "text")).put("result", outcome);
        }
        return out;
    }

    private static ArrayNode requirementList(List<JsonNode> minimum) {
        ArrayNode out = Json.nodes().arrayNode();
        for (JsonNode r : minimum) {
            out.addObject().put("event", strGet(r, "event")).put("class", strGet(r, "class", "any"));
        }
        return out;
    }

    /** What the control's {@code minimum_evidence} requires and which of it this subject's events met:
     * only an {@code insufficient_evidence} outcome can have a gap, which also names any source-class
     * shortfall. */
    private static ObjectNode evidenceGap(Catalog.ControlSpec spec, Assertions.Assertion a, List<JsonNode> subjectEvents) {
        ArrayNode required = requirementList(spec != null ? spec.minimumEvidence : List.of());
        ObjectNode gap = Json.nodes().objectNode();
        gap.set("required", required);
        if (!"insufficient_evidence".equals(a.outcome)) {
            gap.set("observed", required.deepCopy());
            gap.putArray("missing");
            return gap;
        }
        ArrayNode observed = gap.putArray("observed");
        for (JsonNode r : required) {
            if (Assess.requirementMet(subjectEvents, r)) {
                observed.add(r.deepCopy());
            }
        }
        gap.set("missing", spec != null
                ? requirementList(Assess.missingEvidence(spec, subjectEvents, a))
                : Json.nodes().arrayNode());
        return gap;
    }

    private static ObjectNode techniques(Catalog.ControlSpec spec) {
        ObjectNode block = Json.nodes().objectNode();
        ArrayNode list = block.putArray("techniques");
        if (spec == null) {
            return block;
        }
        for (JsonNode ref : asList(rawField(spec, "techniques"))) {
            String text = Readiness.pyStr(ref);
            list.addObject()
                    .put("style", "generic")
                    .put("ref", text)
                    .put("digest", "sha256:" + Canonical.sha256Hex(text.getBytes(StandardCharsets.UTF_8)));
        }
        JsonNode hint = rawField(spec, "remediation_hint");
        if (Readiness.pyTruthy(hint)) {
            block.put("hint", Readiness.pyStr(hint));
        }
        return block;
    }

    private static ObjectNode acceptance(Catalog.ControlSpec spec, String outcome, List<String> reverifyCommand) {
        List<String> texts = new ArrayList<>();
        for (JsonNode e : rawList(spec, "expectations")) {
            if (Readiness.pyTruthy(e.get("text"))) {
                texts.add(strGet(e, "text"));
            }
        }
        String criteria = String.join("; ", texts);
        ObjectNode out = Json.nodes().objectNode();
        out.putObject("expected_transition").put("from", outcome).put("to", "conformant");
        out.put("criteria", criteria.isEmpty() ? "re-run and reach a conformant outcome for this control." : criteria);
        ArrayNode argv = out.putArray("reverify_command");
        reverifyCommand.forEach(argv::add);
        return out;
    }

    private static ObjectNode finding(
            Assertions.Assertion a, Catalog.ControlSpec spec, List<JsonNode> subjectEvents, List<String> reverifyCommand) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("control", a.control);
        out.put("control_version", a.controlVersion);
        out.put("outcome", a.outcome);
        out.put("rung", a.rung);
        out.put("mode", a.mode);
        out.put("severity", severity(spec));
        out.putObject("window").put("start", a.window[0]).put("end", a.window[1]);
        out.put("title", findingTitle(a, spec));
        out.set("clauses", clauses(spec));
        out.set("expectations", expectations(spec, a.outcome));
        out.set("evidence_gap", evidenceGap(spec, a, subjectEvents));
        ArrayNode violations = out.putArray("violations");
        a.violations.forEach(violations::add);
        ArrayNode evidence = out.putArray("evidence");
        a.evidence.forEach(p -> evidence.add(p.toJson()));
        out.set("remediation", techniques(spec));
        out.set("acceptance", acceptance(spec, a.outcome, reverifyCommand));
        out.put("guardrails_ref", GUARDRAILS_REF);
        JsonNode description = rawField(spec, "description");
        if (Readiness.pyTruthy(description)) {
            out.put("description", Readiness.pyStr(description));
        }
        return out;
    }

    private static String notAssessedReason(Catalog.ControlSpec spec, Assertions.Assertion a) {
        int rung = spec != null ? spec.rung : a.rung;
        return rung != 2
                ? "rung " + rung + " is not yet evaluated by this engine"
                : "no structural shape is defined for this control";
    }

    /** The canonical, per-subject remediation package (SPEC §7; Appendix A2 (A)). The caller writes it
     * with the RFC 8785 canonicalizer; {@code $id} is content-derived, so two runs over the same
     * assertions give the same package. */
    static ObjectNode renderRemediationPackage(
            String subject, List<Assertions.Assertion> subjectAssertions, List<Catalog> catalogs,
            String assertionsDigest, List<JsonNode> subjectEvents, List<String> reverifyCommand) {
        Map<String, Catalog.ControlSpec> byControl = Report.controlIndex(catalogs);
        List<Assertions.Assertion> ordered = new ArrayList<>(subjectAssertions);
        ordered.sort((x, y) -> Json.byteCompare(x.control, y.control));

        ObjectNode body = Json.nodes().objectNode();
        body.put("package_version", 1);
        ObjectNode generated = body.putObject("generated_from");
        generated.put("assertions_digest", assertionsDigest);
        generated.putObject("engine")
                .put("impl", Version.ENGINE_NAME)
                .put("version", Version.ENGINE_VERSION)
                .put("spec_version", Version.SPEC_VERSION);
        ArrayNode catalogRefs = generated.putArray("catalogs");
        for (Catalog c : catalogs) {
            catalogRefs.addObject()
                    .put("id", c.id)
                    .put("version", c.version)
                    .put("digest", c.files != null
                            ? Catalog.provenanceDigest(c.files)
                            : Catalog.provenanceDigest(c.directory));
        }
        body.put("subject", subject);
        ArrayNode findings = body.putArray("findings");
        ArrayNode notAssessed = body.putArray("not_assessed");
        for (Assertions.Assertion a : ordered) {
            Catalog.ControlSpec spec = byControl.get(a.control);
            if (FINDING_OUTCOMES.contains(a.outcome)) {
                findings.add(finding(a, spec, subjectEvents, reverifyCommand));
            } else if ("not_assessed".equals(a.outcome)) {
                notAssessed.addObject()
                        .put("control", a.control)
                        .put("control_version", a.controlVersion)
                        .put("title", findingTitle(a, spec))
                        .put("severity", severity(spec))
                        .put("reason", notAssessedReason(spec, a));
            }
        }
        TreeSet<String> deviations = new TreeSet<>(Json::byteCompare);
        for (Assertions.Assertion a : subjectAssertions) {
            if (a.deviation != null && !a.deviation.isEmpty()) {
                deviations.add(a.deviation);
            }
        }
        ArrayNode applied = body.putArray("deviations_applied");
        deviations.forEach(applied::add);

        ObjectNode pkg = Json.nodes().objectNode();
        pkg.put("$schema", SCHEMA_URL);
        pkg.put("$id", "urn:agentce:remediation-package:sha256:" + Canonical.sha256Hex(body));
        pkg.setAll(body);
        return pkg;
    }

    private static ObjectNode asObject(JsonNode value) {
        return value != null && value.isObject() ? (ObjectNode) value : Json.nodes().objectNode();
    }

    private static List<JsonNode> asList(JsonNode value) {
        List<JsonNode> out = new ArrayList<>();
        if (value != null && value.isArray()) {
            value.forEach(out::add);
        }
        return out;
    }

    /** A rebuilt (never mutated) copy of {@code d} with every field in {@code keys} sanitised. */
    private static ObjectNode sanitizeKeys(JsonNode d, String... keys) {
        ObjectNode out = Json.nodes().objectNode().setAll(asObject(d));
        for (String k : keys) {
            out.put(k, Report.sanitizeField(strGet(d, k)));
        }
        return out;
    }

    private static ArrayNode sanitizeEach(JsonNode list, String... keys) {
        ArrayNode out = Json.nodes().arrayNode();
        for (JsonNode item : asList(list)) {
            out.add(sanitizeKeys(item, keys));
        }
        return out;
    }

    private static ObjectNode sanitizeEvidenceGap(JsonNode gap) {
        ObjectNode out = Json.nodes().objectNode();
        for (String key : new String[] {"required", "observed", "missing"}) {
            out.set(key, sanitizeEach(asObject(gap).get(key), "event", "class"));
        }
        return out;
    }

    /** A render-only, rebuilt view of one finding: every catalog- or evidence-derived string the
     * template interpolates is sanitised; the canonical package is untouched. */
    static ObjectNode sanitizeFinding(JsonNode f) {
        ObjectNode out = sanitizeKeys(f, "control", "title", "mode");
        out.set("window", sanitizeKeys(asObject(f.get("window")), "start", "end"));
        out.set("clauses", sanitizeEach(f.get("clauses"), "framework", "clause", "relation"));
        out.set("expectations", sanitizeEach(f.get("expectations"), "text"));
        out.set("evidence_gap", sanitizeEvidenceGap(f.get("evidence_gap")));
        out.set("evidence", sanitizeEach(f.get("evidence"), "ref"));
        out.set("violations", sanitizeEach(f.get("violations"), "path", "constraint", "message_key"));
        ObjectNode remediation = Json.nodes().objectNode().setAll(asObject(f.get("remediation")));
        remediation.set("techniques", sanitizeEach(remediation.get("techniques"), "ref"));
        out.set("remediation", remediation);
        out.set("acceptance", sanitizeKeys(asObject(f.get("acceptance")), "criteria"));
        return out;
    }

    private static ObjectNode mdContext(JsonNode pkg) {
        ObjectNode out = Json.nodes().objectNode().setAll(asObject(pkg));
        out.put("subject", Report.sanitizeField(strGet(pkg, "subject")));
        ArrayNode findings = out.putArray("findings");
        for (JsonNode f : asList(pkg.get("findings"))) {
            findings.add(sanitizeFinding(f));
        }
        out.set("not_assessed", sanitizeEach(pkg.get("not_assessed"), "control", "title"));
        ArrayNode deviations = out.putArray("deviations_applied");
        for (JsonNode d : asList(pkg.get("deviations_applied"))) {
            deviations.add(Report.sanitizeField(Readiness.pyStr(d)));
        }
        return out;
    }

    /** Render the Markdown prompt from a canonical remediation package (SPEC §7; Appendix A2 (B)) by
     * the one language-neutral template: a pure function of the package's content. */
    static String renderRemediationMd(JsonNode pkg) {
        return Templating.render(remediationTemplate(), mdContext(pkg));
    }
}
