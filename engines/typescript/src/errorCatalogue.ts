/**
 * The error-key registry (SPEC §13.4 AX-6), a port of Python's `agentce.error_catalogue`: every
 * `errors.<key>.cause|fix` pair in the vendored catalogue (`data/i18n/`, sourced from `spec/i18n/`)
 * becomes one entry, and {@link catalogueGaps} names any key a raise site uses that the catalogue
 * lacks. Raise sites keep their own run-time cause and fix text, as Python's do; the catalogue holds
 * the generic, documented text `docs/errors.md` is rendered from. Mirrors `ErrorCatalogue.java`.
 */

import { DEFAULT_LANGUAGE, loadCatalog } from "./messages";

export interface CatalogueEntry {
  readonly cause: string;
  readonly fix: string;
}

function loadMessageKeys(): ReadonlyMap<string, CatalogueEntry> {
  const catalog = loadCatalog(DEFAULT_LANGUAGE);
  const entries = new Map<string, CatalogueEntry>();
  for (const name of Object.keys(catalog).sort()) {
    const key = /^errors\.(.+)\.(?:cause|fix)$/.exec(name)?.[1];
    if (key !== undefined && !entries.has(key)) {
      entries.set(key, {
        cause: catalog[`errors.${key}.cause`] ?? "",
        fix: catalog[`errors.${key}.fix`] ?? "",
      });
    }
  }
  return entries;
}

let cached: ReadonlyMap<string, CatalogueEntry> | undefined;

/** key -> {cause, fix}, built from the vendored catalogue on first use. */
export function messageKeys(): ReadonlyMap<string, CatalogueEntry> {
  cached ??= loadMessageKeys();
  return cached;
}

/** The English cause text the catalogue holds for error `key`, the same text Python's
 * `error_catalogue.MESSAGE_KEYS[key].cause` reads. Throws if the key has none, so a missing entry
 * fails loudly rather than printing an empty reason. */
export function errorCause(key: string): string {
  const cause = messageKeys().get(key)?.cause;
  if (!cause) {
    throw new Error(`message catalogue has no errors.${key}.cause`);
  }
  return cause;
}

/** The English fix text the catalogue holds for error `key`; throws if missing. */
export function errorFix(key: string): string {
  const fix = messageKeys().get(key)?.fix;
  if (!fix) {
    throw new Error(`message catalogue has no errors.${key}.fix`);
  }
  return fix;
}

/** A literal key passed as the first argument of an error constructor or helper. Keys composed at
 * run time (`` `input.${key}_missing` ``) are template literals and are not matched, as in Python. */
const RAISED = /(?:new\s+(?:InputError|AgentceError)|unreadableError)\(\s*"([^"]+)"/g;

/** The completeness check: a key with an empty cause or fix, and any key raised in `sources` (the
 * engine's own source texts) that the catalogue lacks. Empty when the catalogue covers them all. */
export function catalogueGaps(sources: Iterable<string>): string[] {
  const keys = messageKeys();
  const problems: string[] = [];
  for (const [key, entry] of keys) {
    if (!entry.cause.trim()) problems.push(`${key}: no cause`);
    if (!entry.fix.trim()) problems.push(`${key}: no fix`);
  }
  const raised = new Set<string>();
  for (const text of sources) {
    for (const match of text.matchAll(RAISED)) raised.add(match[1]);
  }
  for (const key of [...raised].sort()) {
    if (!keys.has(key)) problems.push(`${key}: raised but not in the catalogue`);
  }
  return problems;
}
