/**
 * Assertions: one machine-readable record per (control, subject) (SPEC §9.4, §9.2).
 *
 * `assertions.json` is the source of truth from which report.md/html, OSCAL, and SARIF are rendered; a
 * translation never changes it, and after RFC 8785 canonicalisation it is byte-identical across
 * engines. Every non-`not_applicable` verdict that asserts support (`conformant`, `non-conformant`,
 * `partial`) must cite at least one evidence pointer; the report generator refuses to emit one that
 * does not (DC-5). This is a faithful port of the Python reference.
 */

import { AgentceError } from "./errors";
import { ExitCode } from "./exitCodes";

export const OUTCOMES = [
  "conformant",
  "non-conformant",
  "partial",
  "not_applicable",
  "not_assessed",
  "insufficient_evidence",
] as const;

/** Outcomes that assert support and therefore require evidence pointers (DC-5). */
const REQUIRE_EVIDENCE = new Set(["conformant", "non-conformant", "partial"]);

export interface EvidencePointer {
  ref: string;
  digest: string;
  sourceClass: string;
}

export function evidencePointerToJson(pointer: EvidencePointer): Record<string, string> {
  return { ref: pointer.ref, digest: pointer.digest, source_class: pointer.sourceClass };
}

export interface Assertion {
  control: string;
  controlVersion: string;
  subject: string;
  outcome: string;
  rung: number;
  mode: string;
  window: [string, string];
  population: [number, number];
  severity: string;
  family: string;
  expectations: Array<Record<string, string>>;
  violations: Array<Record<string, string>>;
  evidence: EvidencePointer[];
  sourceClassSatisfied: boolean | null;
  evidenceStrength: string | null;
  deviation: string | null;
  crosswalk: Array<Record<string, string | boolean>>;
}

/** Build an assertion, defaulting the optional collections and nullable fields (mirrors the dataclass). */
export function makeAssertion(
  fields: Pick<
    Assertion,
    | "control"
    | "controlVersion"
    | "subject"
    | "outcome"
    | "rung"
    | "mode"
    | "window"
    | "population"
    | "severity"
    | "family"
  > &
    Partial<Assertion>,
): Assertion {
  return {
    expectations: [],
    violations: [],
    evidence: [],
    sourceClassSatisfied: null,
    evidenceStrength: null,
    deviation: null,
    crosswalk: [],
    ...fields,
  };
}

export function assertionToJson(assertion: Assertion): Record<string, unknown> {
  const record: Record<string, unknown> = {
    control: assertion.control,
    control_version: assertion.controlVersion,
    subject: assertion.subject,
    outcome: assertion.outcome,
    rung: assertion.rung,
    mode: assertion.mode,
    window: { start: assertion.window[0], end: assertion.window[1] },
    population: { applicable: assertion.population[0], failed: assertion.population[1] },
    severity: assertion.severity,
    family: assertion.family,
  };
  if (assertion.expectations.length > 0) {
    record.expectations = assertion.expectations;
  }
  if (assertion.violations.length > 0) {
    record.violations = assertion.violations;
  }
  if (assertion.evidence.length > 0) {
    record.evidence = assertion.evidence.map(evidencePointerToJson);
  }
  if (assertion.sourceClassSatisfied !== null) {
    record.source_class_satisfied = assertion.sourceClassSatisfied;
  }
  if (assertion.evidenceStrength !== null) {
    record.evidence_strength = assertion.evidenceStrength;
  }
  if (assertion.deviation !== null) {
    record.deviation = assertion.deviation;
  }
  if (assertion.crosswalk.length > 0) {
    record.crosswalk = assertion.crosswalk;
  }
  return record;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function evidencePointerFromJson(data: unknown): EvidencePointer {
  const d = isRecord(data) ? data : {};
  return {
    ref: String(d.ref ?? ""),
    digest: String(d.digest ?? ""),
    sourceClass: String(d.source_class ?? ""),
  };
}

/** Reconstruct an assertion from its JSON form (the inverse of {@link assertionToJson}), for
 * re-rendering a report from a committed `assertions.json` (SPEC §9.4). */
export function assertionFromJson(data: unknown): Assertion {
  const d = isRecord(data) ? data : {};
  const window = isRecord(d.window) ? d.window : {};
  const population = isRecord(d.population) ? d.population : {};
  return {
    control: String(d.control ?? ""),
    controlVersion: String(d.control_version ?? ""),
    subject: String(d.subject ?? ""),
    outcome: String(d.outcome ?? ""),
    rung: Number(d.rung ?? 0),
    mode: String(d.mode ?? ""),
    window: [String(window.start ?? ""), String(window.end ?? "")],
    population: [Number(population.applicable ?? 0), Number(population.failed ?? 0)],
    severity: String(d.severity ?? ""),
    family: String(d.family ?? ""),
    expectations: Array.isArray(d.expectations)
      ? (d.expectations as Array<Record<string, string>>)
      : [],
    violations: Array.isArray(d.violations) ? (d.violations as Array<Record<string, string>>) : [],
    evidence: Array.isArray(d.evidence) ? d.evidence.map(evidencePointerFromJson) : [],
    sourceClassSatisfied:
      typeof d.source_class_satisfied === "boolean" ? d.source_class_satisfied : null,
    evidenceStrength: typeof d.evidence_strength === "string" ? d.evidence_strength : null,
    deviation: typeof d.deviation === "string" ? d.deviation : null,
    crosswalk: Array.isArray(d.crosswalk)
      ? (d.crosswalk as Array<Record<string, string | boolean>>)
      : [],
  };
}

/** Return the six-outcome counts (SPEC §9.2); every outcome is present, even at zero. */
export function aggregate(assertions: Assertion[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const outcome of OUTCOMES) {
    counts[outcome] = 0;
  }
  for (const assertion of assertions) {
    counts[assertion.outcome] = (counts[assertion.outcome] ?? 0) + 1;
  }
  return counts;
}

/** Refuse to emit a supporting verdict with no evidence pointer (DC-5); raise on the first. */
export function checkDc5(assertions: Assertion[]): void {
  for (const assertion of assertions) {
    if (REQUIRE_EVIDENCE.has(assertion.outcome) && assertion.evidence.length === 0) {
      throw new AgentceError(
        "report.missing_evidence_pointer",
        `control ${assertion.control} on ${assertion.subject} is ${assertion.outcome} but cites no evidence pointer.`,
        "every conformant, non-conformant, or partial outcome must cite evidence (DC-5).",
        ExitCode.INPUT_ERROR,
      );
    }
  }
}
