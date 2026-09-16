/**
 * Applicability resolution (SPEC §6.5, §7.3, IR-11).
 *
 * For each subject the resolver derives the enterprise roles (IR-11: an in-house or substantially
 * modified system is `both` provider and deployer; an unmodified third-party system is `deployer`),
 * selects the controls that apply to those roles (`applies_to_roles`) from the declared catalogs and
 * overlays, and records why. It reconciles the declared scope against what the evidence shows and
 * raises drift findings -- an observed decision type that was never declared, a bundle component that
 * was never declared, or an event from a subject the profile does not name -- and carries the
 * trust-class justifications so an assessor can challenge them. This is a faithful port of the Python
 * reference.
 */

import type { Profile, Subject } from "./profile";
import { byteCompare } from "./util";

export const ROLES = ["deployer", "provider"] as const;

/** The applicability-relevant metadata of a catalog control. */
export interface ControlMeta {
  id: string;
  appliesToRoles: string[];
  family: string;
}

type Event = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Expand a declared role to concrete roles (`both` -> deployer and provider), per IR-11. */
export function effectiveRoles(role: string | null): string[] {
  if (role === "both") {
    return [...ROLES];
  }
  if (role === "deployer" || role === "provider") {
    return [role];
  }
  return ["deployer"]; // the conservative default when a subject does not declare its role
}

function roleApplies(subjectRoles: Set<string>, appliesToRoles: string[]): boolean {
  const targets = new Set(appliesToRoles);
  if (targets.has("both")) {
    for (const r of ROLES) {
      targets.add(r);
    }
  }
  for (const r of subjectRoles) {
    if (targets.has(r)) {
      return true;
    }
  }
  return false;
}

function expanded(appliesToRoles: string[]): Set<string> {
  const targets = new Set(appliesToRoles);
  if (targets.has("both")) {
    for (const r of ROLES) {
      targets.add(r);
    }
  }
  return new Set([...targets].filter((r) => (ROLES as readonly string[]).includes(r)));
}

function eventType(event: Event): string {
  const data = event.data;
  return isRecord(data) && typeof data["@type"] === "string" ? data["@type"] : "";
}

function observedDecisionTypes(subjectId: string, events: Event[]): string[] {
  const observed = new Set<string>();
  for (const event of events) {
    if (String(event.subject ?? "") !== subjectId || eventType(event) !== "Decision") {
      continue;
    }
    const data = event.data;
    if (isRecord(data) && typeof data.decision_type === "string") {
      observed.add(data.decision_type);
    }
  }
  return [...observed].sort(byteCompare);
}

function observedComponents(subjectId: string, events: Event[]): Set<string> {
  const observed = new Set<string>();
  for (const event of events) {
    if (String(event.subject ?? "") !== subjectId) {
      continue;
    }
    const data = event.data;
    if (!isRecord(data)) {
      continue;
    }
    if (eventType(event) === "Component") {
      for (const key of ["name", "id"]) {
        if (typeof data[key] === "string") {
          observed.add(data[key] as string);
        }
      }
    }
    if (eventType(event) === "BundleLoaded") {
      const components = Array.isArray(data.components) ? data.components : [];
      for (const component of components) {
        if (typeof component === "string") {
          observed.add(component);
        } else if (isRecord(component) && typeof component.name === "string") {
          observed.add(component.name);
        }
      }
    }
  }
  return observed;
}

function justification(subject: Subject, sourceId: string): string {
  return `declared for ${sourceId} in the applicability profile of ${subject.id}`;
}

function classJustifications(subject: Subject): Array<Record<string, string>> {
  const out: Array<Record<string, string>> = [];
  for (const source of subject.evidenceSources) {
    if (source.cls) {
      out.push({
        source: source.source,
        class: source.cls,
        justification: justification(subject, source.source),
      });
    }
  }
  return out;
}

/** Resolve the applicability statement for one subject. */
export function resolveSubject(
  subject: Subject,
  events: Event[],
  controls: ControlMeta[],
  catalogs: string[],
): Record<string, unknown> {
  const roles = effectiveRoles(subject.role);
  const roleSet = new Set(roles);
  const controlRows: Array<Record<string, unknown>> = [];
  for (const control of [...controls].sort((a, b) => byteCompare(a.id, b.id))) {
    const applicable = roleApplies(roleSet, control.appliesToRoles);
    const inBoth = expanded(control.appliesToRoles);
    controlRows.push({
      id: control.id,
      applicable,
      reason_code: applicable ? "role_match" : "role_mismatch",
      reason_refs: [...roleSet].filter((r) => inBoth.has(r)).sort(byteCompare),
    });
  }

  const observed = observedDecisionTypes(subject.id, events);
  const declared = [...new Set(subject.declaredDecisionTypes)].sort(byteCompare);
  const drift: Array<Record<string, string>> = [];
  for (const decisionType of observed) {
    if (!declared.includes(decisionType)) {
      drift.push({ kind: "undeclared_decision_type", ref: decisionType });
    }
  }
  const declaredComponents = new Set(subject.declaredComponents);
  const extraComponents = [...observedComponents(subject.id, events)]
    .filter((c) => !declaredComponents.has(c))
    .sort(byteCompare);
  for (const component of extraComponents) {
    drift.push({ kind: "undeclared_component", ref: component });
  }

  return {
    subject: subject.id,
    roles,
    catalogs: [...catalogs],
    controls: controlRows,
    observed_decision_types: observed,
    declared_decision_types: declared,
    drift,
    class_justifications: classJustifications(subject),
  };
}

/**
 * Resolve one applicability statement per declared subject.
 *
 * An event whose `subject` names no declared subject is an `undeclared_subject` drift finding, recorded
 * on the first subject's statement (a bundle-level drift with no subject of its own).
 */
export function resolve(
  profile: Profile,
  events: Event[],
  controls: ControlMeta[] = [],
): Array<Record<string, unknown>> {
  const statements = profile.subjects.map((s) =>
    resolveSubject(s, events, controls, profile.catalogs),
  );

  const declaredIds = new Set(profile.subjects.map((s) => s.id));
  const observedSubjects = new Set(
    events.map((e) => String(e.subject ?? "")).filter((s) => s !== ""),
  );
  const undeclared = [...observedSubjects].filter((s) => !declaredIds.has(s)).sort(byteCompare);
  if (undeclared.length > 0 && statements.length > 0) {
    const drift = (statements[0] as Record<string, unknown>).drift as Array<Record<string, string>>;
    for (const s of undeclared) {
      drift.push({ kind: "undeclared_subject", ref: s });
    }
  }
  return statements;
}
