/**
 * Message-key catalogue for report rendering (SPEC §9.3, §8.4).
 *
 * All human-readable text in `report.md` and `report.html` comes from message keys, read from the
 * vendored, language-neutral catalogue (`data/i18n/`, sourced from `spec/i18n/`, `bundledData.test.ts`
 * holds them in sync) that also backs Python's `agentce.error_catalogue` CLI strings, so a translation
 * changes only the report -- never `assertions.json`, the manifest digests, or the claim. v1 ships the
 * `en` catalogue complete; a partial `de` catalogue demonstrates the translation mechanism (any key it
 * omits falls back to `en`). The report language is recorded in the manifest as `run.report_language`
 * and has no effect on the machine-readable outputs. A faithful port of the Python reference
 * (`engines/python/agentce/messages.py`, `i18n_format.py`); mirrors `engines/java/.../Messages.java`.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { i18nDir } from "./bundled";

export const DEFAULT_LANGUAGE = "en";

/** Which of the catalogue's keys this module renders. Their text lives in the vendored catalogue,
 * never hardcoded in this module. */
const REPORT_KEY_PREFIXES = ["report.", "verdict.", "next.", "outcome.", "readiness."];

/** `loadCatalog` is read-and-filter per language; a report render calls `catalogue()` several times,
 * so the parsed-and-filtered result is cached per language rather than re-reading the file each time. */
const reportKeyCache = new Map<string, Record<string, string>>();

function loadCatalog(language: string): Record<string, string> {
  const path = join(i18nDir(), `messages.${language}.json`);
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as Record<string, string>;
  } catch {
    return {};
  }
}

function reportKeys(language: string): Record<string, string> {
  const cached = reportKeyCache.get(language);
  if (cached) return cached;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(loadCatalog(language))) {
    if (REPORT_KEY_PREFIXES.some((prefix) => key.startsWith(prefix))) {
      out[key] = value;
    }
  }
  reportKeyCache.set(language, out);
  return out;
}

/** The message catalogue for `language`, backed by `en` for any missing key. */
export function catalogue(language: string = DEFAULT_LANGUAGE): Record<string, string> {
  return { ...reportKeys("en"), ...reportKeys(language) };
}
