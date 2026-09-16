/**
 * The applicability profile (SPEC §6.5): what the adopter declares about the assessed system.
 *
 * The profile names the observation window, the subjects (agents) under assessment, each subject's
 * evidence sources and their trust classes, the independent coverage denominators, the declared
 * decision types and oversight modalities, and the catalogs to apply. This module parses the normative
 * fields the coverage and applicability stages need; unknown fields are ignored so the profile can
 * carry more than one engine version reads. This is a faithful port of the Python reference.
 */

import { readFileSync } from "node:fs";
import { load } from "js-yaml";

export interface EvidenceSource {
  adapter: string;
  source: string;
  cls: string;
  manifest: string | null;
}

export interface CoverageDenominator {
  kind: string;
  source: string;
  covers: string[];
  manifest: string | null;
  statement: string | null;
}

export interface Subject {
  id: string;
  name: string | null;
  role: string | null;
  evidenceSources: EvidenceSource[];
  coverageDenominators: CoverageDenominator[];
  declaredDecisionTypes: string[];
  declaredOversight: Record<string, string>;
  declaredComponents: string[];
}

export interface Profile {
  profileVersion: number;
  observationWindow: Record<string, string>;
  subjects: Subject[];
  catalogs: string[];
}

/** The independent coverage-denominator source ids of a subject. */
export function denominatorIds(subject: Subject): Set<string> {
  return new Set(subject.coverageDenominators.map((d) => d.source));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strMap(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (isRecord(value)) {
    for (const [key, val] of Object.entries(value)) {
      out[String(key)] = String(val);
    }
  }
  return out;
}

function opt(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function parseSubject(raw: Record<string, unknown>): Subject {
  const subject: Subject = {
    id: String(raw.id),
    name: opt(raw.name),
    role: opt(raw.role),
    evidenceSources: [],
    coverageDenominators: [],
    declaredDecisionTypes: asArray(raw.declared_decision_types).map(String),
    declaredOversight: strMap(raw.declared_oversight),
    declaredComponents: asArray(raw.third_party_components)
      .filter((c): c is Record<string, unknown> => isRecord(c) && "name" in c)
      .map((c) => String(c.name)),
  };
  for (const entry of asArray(raw.evidence_sources)) {
    if (isRecord(entry) && "source" in entry) {
      subject.evidenceSources.push({
        adapter: String(entry.adapter ?? ""),
        source: String(entry.source),
        cls: String(entry.class ?? ""),
        manifest: opt(entry.manifest),
      });
    }
  }
  for (const entry of asArray(raw.coverage_denominators)) {
    if (isRecord(entry) && "source" in entry) {
      subject.coverageDenominators.push({
        kind: String(entry.kind ?? ""),
        source: String(entry.source),
        covers: asArray(entry.covers).map(String),
        manifest: opt(entry.manifest),
        statement: opt(entry.statement),
      });
    }
  }
  return subject;
}

export function profileFromDict(data: Record<string, unknown>): Profile {
  const profile: Profile = {
    profileVersion: Number(data.profile_version ?? 1),
    observationWindow: strMap(data.observation_window),
    subjects: [],
    catalogs: asArray(data.catalogs).map(String),
  };
  for (const raw of asArray(data.subjects)) {
    if (isRecord(raw) && "id" in raw) {
      profile.subjects.push(parseSubject(raw));
    }
  }
  return profile;
}

export function loadProfile(path: string): Profile {
  const data = load(readFileSync(path, "utf-8"));
  if (!isRecord(data)) {
    return { profileVersion: 1, observationWindow: {}, subjects: [], catalogs: [] };
  }
  return profileFromDict(data);
}
