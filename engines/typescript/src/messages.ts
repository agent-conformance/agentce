/**
 * Message-key catalogue for report rendering (SPEC §9.3, §8.4).
 *
 * All human-readable text in `report.md` and `report.html` comes from message keys, so a translation
 * changes only the report -- never `assertions.json`, the manifest digests, or the claim. v1 ships the
 * `en` catalogue; a partial `de` catalogue demonstrates the translation mechanism (any key it omits
 * falls back to `en`). The report language is recorded in the manifest as `run.report_language` and has
 * no effect on the machine-readable outputs. Mirrors `engines/python/agentce/messages.py` and
 * `engines/java/.../Messages.java`.
 */

export const DEFAULT_LANGUAGE = "en";

const EN: Record<string, string> = {
  "report.title": "AgentCE conformance report",
  "report.summary_heading": "Outcome summary",
  "report.assertions_heading": "Assertions",
  "report.no_controls": "No controls were evaluated.",
};

/** A partial translation, to exercise the mechanism; missing keys fall back to `en`. */
const DE: Record<string, string> = {
  "report.title": "AgentCE-Konformitätsbericht",
  "report.summary_heading": "Ergebnisübersicht",
  "report.assertions_heading": "Aussagen",
  "report.no_controls": "Es wurden keine Kontrollen bewertet.",
};

const CATALOGUES: Record<string, Record<string, string>> = { en: EN, de: DE };

/** The message catalogue for `language`, backed by `en` for any missing key. */
export function catalogue(language: string = DEFAULT_LANGUAGE): Record<string, string> {
  return { ...EN, ...CATALOGUES[language] };
}
