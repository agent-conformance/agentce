/**
 * The AI-actionable remediation package and its Markdown prompt (SPEC §7; Appendix A2 (A) and (B)).
 *
 * A faithful port of the Python reference's `render_remediation_package` and `render_remediation_md`
 * (`report.py`): a pure, deterministic join of each non-passing control's catalog data with that
 * control's real assertion outcome, and the one language-neutral template
 * (`spec/report/templates/remediation.md.tmpl`, vendored under `data/templates/`) rendered over a
 * sanitised copy of the package. Catalog values are read with Python's `str()` and truthiness, so
 * every engine writes the same bytes.
 */

import { createHash } from "node:crypto";
import type { Assertion } from "./assertions";
import { evidencePointerToJson } from "./assertions";
import { missingEvidence, requirementMet } from "./assess";
import { remediationTemplate } from "./bundled";
import { sha256Hex } from "./canonical";
import type { Catalog, ControlSpec } from "./catalog";
import type { Event } from "./graph";
import { pyTruthy } from "./readiness";
import { catalogProvenanceDigest, controlIndex, sanitizeField } from "./report";
import { render } from "./templating";
import { byteCompare, pyStr } from "./util";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

type Json = Record<string, unknown>;

/** Outcomes a package reports as a finding; `not_assessed` gets its own array. */
const FINDING_OUTCOMES = new Set(["non-conformant", "partial", "insufficient_evidence"]);
/** The fixed guardrail block every finding cites by reference. */
const GUARDRAILS_REF = "remediation.guardrails.v1";
const SEVERITY_LEVELS = new Set(["high", "medium", "low"]);
const SCHEMA_URL = "https://agent-conformance.org/spec/report/remediation-package.schema.json";

/** Python's `str(d.get(key, fallback))`: an absent key gives `fallback`, a present one its `str()`. */
function strGet(d: Json, key: string, fallback = ""): string {
  return Object.hasOwn(d, key) ? pyStr(d[key]) : fallback;
}

/** Python's `spec.raw.get(key, []) or []` over a catalog list field. */
function rawList(spec: ControlSpec | undefined, key: string): Json[] {
  const value = spec?.raw[key];
  return Array.isArray(value) ? (value as Json[]) : [];
}

function severity(spec: ControlSpec | undefined): string {
  return spec !== undefined && SEVERITY_LEVELS.has(spec.severity) ? spec.severity : "unrated";
}

function findingTitle(a: Assertion, spec: ControlSpec | undefined): string {
  return spec?.title ? spec.title : a.control;
}

/** Every crosswalk clause as the catalog carries it; only a JSON/YAML `true` counts as verified. */
function clauses(spec: ControlSpec | undefined): Json[] {
  return rawList(spec, "crosswalk").map((entry) => ({
    framework: strGet(entry, "framework"),
    clause: strGet(entry, "clause"),
    relation: strGet(entry, "relation"),
    verified_against_text: entry.verified_against_text === true,
  }));
}

function expectations(spec: ControlSpec | undefined, outcome: string): Json[] {
  return rawList(spec, "expectations").map((e) => ({
    id: strGet(e, "id"),
    text: strGet(e, "text"),
    result: outcome,
  }));
}

function requirementList(minimum: Array<Record<string, unknown>>): Array<Record<string, string>> {
  return minimum.map((r) => ({ event: strGet(r, "event"), class: strGet(r, "class", "any") }));
}

/** What the control's `minimum_evidence` requires and which of it this subject's events met: only an
 * `insufficient_evidence` outcome can have a gap, which also names any source-class shortfall. */
function evidenceGap(spec: ControlSpec | undefined, a: Assertion, subjectEvents: Event[]): Json {
  const required = requirementList(spec?.minimumEvidence ?? []);
  if (a.outcome !== "insufficient_evidence") {
    return { required, observed: [...required], missing: [] };
  }
  return {
    required,
    observed: required.filter((r) => requirementMet(subjectEvents, r)),
    missing: spec !== undefined ? requirementList(missingEvidence(spec, subjectEvents, a)) : [],
  };
}

function techniques(spec: ControlSpec | undefined): Json {
  if (spec === undefined) {
    return { techniques: [] };
  }
  const refs: unknown[] = Array.isArray(spec.raw.techniques) ? spec.raw.techniques : [];
  const block: Json = {
    techniques: refs.map((ref) => ({
      style: "generic",
      ref: pyStr(ref),
      digest: `sha256:${createHash("sha256").update(pyStr(ref), "utf-8").digest("hex")}`,
    })),
  };
  if (pyTruthy(spec.raw.remediation_hint)) {
    block.hint = pyStr(spec.raw.remediation_hint);
  }
  return block;
}

function acceptance(
  spec: ControlSpec | undefined,
  outcome: string,
  reverifyCommand: string[],
): Json {
  const texts = rawList(spec, "expectations")
    .filter((e) => pyTruthy(e.text))
    .map((e) => strGet(e, "text"));
  return {
    expected_transition: { from: outcome, to: "conformant" },
    criteria: texts.join("; ") || "re-run and reach a conformant outcome for this control.",
    reverify_command: [...reverifyCommand],
  };
}

function finding(
  a: Assertion,
  spec: ControlSpec | undefined,
  subjectEvents: Event[],
  reverifyCommand: string[],
): Json {
  const out: Json = {
    control: a.control,
    control_version: a.controlVersion,
    outcome: a.outcome,
    rung: a.rung,
    mode: a.mode,
    severity: severity(spec),
    window: { start: a.window[0], end: a.window[1] },
    title: findingTitle(a, spec),
    clauses: clauses(spec),
    expectations: expectations(spec, a.outcome),
    evidence_gap: evidenceGap(spec, a, subjectEvents),
    violations: [...a.violations],
    evidence: a.evidence.map(evidencePointerToJson),
    remediation: techniques(spec),
    acceptance: acceptance(spec, a.outcome, reverifyCommand),
    guardrails_ref: GUARDRAILS_REF,
  };
  if (pyTruthy(spec?.raw.description)) {
    out.description = pyStr(spec?.raw.description);
  }
  return out;
}

function notAssessedReason(spec: ControlSpec | undefined, a: Assertion): string {
  const rung = spec !== undefined ? spec.rung : a.rung;
  return rung !== 2
    ? `rung ${rung} is not yet evaluated by this engine`
    : "no structural shape is defined for this control";
}

/**
 * The canonical, per-subject remediation package (SPEC §7; Appendix A2 (A)). The caller writes it
 * with the RFC 8785 canonicalizer; `$id` is content-derived, so two runs over the same assertions
 * give the same package.
 */
export function renderRemediationPackage(
  subject: string,
  subjectAssertions: Assertion[],
  catalogs: Catalog[],
  assertionsDigest: string,
  subjectEvents: Event[],
  reverifyCommand: string[],
): Json {
  const byControl = controlIndex(catalogs);
  const ordered = [...subjectAssertions].sort((x, y) => byteCompare(x.control, y.control));
  const body: Json = {
    package_version: 1,
    generated_from: {
      assertions_digest: assertionsDigest,
      engine: { impl: ENGINE_NAME, version: engineVersion(), spec_version: SPEC_VERSION },
      catalogs: catalogs.map((c) => ({
        id: c.id,
        version: c.version,
        digest: catalogProvenanceDigest(c.files ?? c.directory),
      })),
    },
    subject,
    findings: ordered
      .filter((a) => FINDING_OUTCOMES.has(a.outcome))
      .map((a) => finding(a, byControl.get(a.control), subjectEvents, reverifyCommand)),
    not_assessed: ordered
      .filter((a) => a.outcome === "not_assessed")
      .map((a) => {
        const spec = byControl.get(a.control);
        return {
          control: a.control,
          control_version: a.controlVersion,
          title: findingTitle(a, spec),
          severity: severity(spec),
          reason: notAssessedReason(spec, a),
        };
      }),
    deviations_applied: [
      ...new Set(subjectAssertions.flatMap((a) => (a.deviation ? [a.deviation] : []))),
    ].sort(byteCompare),
  };
  return {
    $schema: SCHEMA_URL,
    $id: `urn:agentce:remediation-package:sha256:${sha256Hex(body)}`,
    ...body,
  };
}

function asJson(value: unknown): Json {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Json)
    : {};
}

function asList(value: unknown): Json[] {
  return Array.isArray(value) ? (value as Json[]) : [];
}

/** A rebuilt (never mutated) copy of `d` with every field in `keys` sanitised. */
function sanitizeKeys(d: Json, keys: string[]): Json {
  return { ...d, ...Object.fromEntries(keys.map((k) => [k, sanitizeField(strGet(d, k))])) };
}

function sanitizeEvidenceGap(gap: Json): Json {
  return Object.fromEntries(
    ["required", "observed", "missing"].map((key) => [
      key,
      asList(gap[key]).map((r) => sanitizeKeys(r, ["event", "class"])),
    ]),
  );
}

/** A render-only, rebuilt view of one finding: every catalog- or evidence-derived string the
 * template interpolates is sanitised; the canonical package is untouched. */
export function sanitizeFinding(f: Json): Json {
  const remediation = asJson(f.remediation);
  return {
    ...sanitizeKeys(f, ["control", "title", "mode"]),
    window: sanitizeKeys(asJson(f.window), ["start", "end"]),
    clauses: asList(f.clauses).map((c) => sanitizeKeys(c, ["framework", "clause", "relation"])),
    expectations: asList(f.expectations).map((e) => sanitizeKeys(e, ["text"])),
    evidence_gap: sanitizeEvidenceGap(asJson(f.evidence_gap)),
    evidence: asList(f.evidence).map((e) => sanitizeKeys(e, ["ref"])),
    violations: asList(f.violations).map((v) =>
      sanitizeKeys(v, ["path", "constraint", "message_key"]),
    ),
    remediation: {
      ...remediation,
      techniques: asList(remediation.techniques).map((t) => sanitizeKeys(t, ["ref"])),
    },
    acceptance: sanitizeKeys(asJson(f.acceptance), ["criteria"]),
  };
}

function mdContext(pkg: Json): Json {
  return {
    ...pkg,
    subject: sanitizeField(strGet(pkg, "subject")),
    findings: asList(pkg.findings).map(sanitizeFinding),
    not_assessed: asList(pkg.not_assessed).map((n) => sanitizeKeys(n, ["control", "title"])),
    deviations_applied: asList(pkg.deviations_applied).map((d: unknown) => sanitizeField(pyStr(d))),
  };
}

/** Render the Markdown prompt from a canonical remediation package (SPEC §7; Appendix A2 (B)) by the
 * one language-neutral template: a pure function of the package's content. */
export function renderRemediationMd(pkg: Json): string {
  return render(remediationTemplate(), mdContext(pkg));
}
