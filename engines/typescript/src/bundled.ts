/**
 * Where the engine finds the data it ships (SPEC §13.4 AX-1).
 *
 * The base and overlay catalogs and the quickstart project are vendored under `data/`, resolved
 * relative to this module so an installed package resolves them from wherever it is installed and a
 * source checkout resolves the same bytes. `bundledData.test.ts` holds the vendored tree
 * byte-identical to its `spec/`/`corpus/` original. This is a faithful port of the Python reference
 * (`bundled.py`).
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { load } from "js-yaml";
import { byteCompare } from "./util";

/** The cross-standard baseline a run evaluates when nothing names a catalog (Python's
 * `bundled.DEFAULT_LENS`). */
export const DEFAULT_LENS = "baseline@2026.09";

function dataRoot(): string {
  return join(__dirname, "..", "data");
}

/** The vendored catalogs, laid out as `base/<catalog>/` and `overlays/<catalog>/`. */
export function catalogsDir(): string {
  return join(dataRoot(), "catalogs");
}

/** Every catalog the engine ships (base and sector overlays), keyed `id@version`. */
export function vendoredCatalogs(): Map<string, string> {
  const found = new Map<string, string>();
  const root = catalogsDir();
  for (const kind of ["base", "overlays"]) {
    const dir = join(root, kind);
    let names: string[] = [];
    try {
      names = readdirSync(dir).sort(byteCompare);
    } catch {
      names = [];
    }
    for (const name of names) {
      const catalogYaml = join(dir, name, "catalog.yaml");
      if (!existsSync(catalogYaml)) {
        continue;
      }
      try {
        const meta = load(readFileSync(catalogYaml, "utf-8")) as Record<string, unknown> | null;
        if (meta && typeof meta.id === "string" && typeof meta.version === "string") {
          found.set(`${meta.id}@${meta.version}`, join(dir, name));
        }
      } catch {
        // a malformed vendored catalog is skipped, never fatal to discovery
      }
    }
  }
  return found;
}

/** The `id@version` of every vendored base catalog, in byte order: the lenses a run can choose
 * (Python's `bundled.base_lenses`). */
export function baseLenses(): string[] {
  const base = join(catalogsDir(), "base");
  return [...vendoredCatalogs()]
    .filter(([, directory]) => directory.startsWith(`${base}/`))
    .map(([label]) => label)
    .sort(byteCompare);
}

/** The vendored quickstart project: an evidence bundle, an applicability profile, and a domain. */
export function quickstartDir(): string {
  return join(dataRoot(), "corpus", "quickstart");
}

/** The vendored Portable Shape Profile term lists (the original is `spec/rules/psp-terms.json`). */
export function pspTermsPath(): string {
  return join(dataRoot(), "psp-terms.json");
}

/** The vendored SPEC 8.5 command-line grammar (the original is `spec/cli/argv-grammar.json`). */
export function argvGrammarPath(): string {
  return join(dataRoot(), "cli", "argv-grammar.json");
}

/** The vendored ICU MessageFormat catalogue (the language-neutral original lives in `spec/i18n/`). */
export function i18nDir(): string {
  return join(dataRoot(), "i18n");
}

/** A vendored report template (each original is `spec/report/templates/<name>`). */
export function templatePath(name: string): string {
  return join(dataRoot(), "templates", name);
}

const cachedTemplates = new Map<string, string>();

/** A vendored report template's text, read once (Python's `bundled.*_template()`). */
function template(name: string): string {
  let text = cachedTemplates.get(name);
  if (text === undefined) {
    text = readFileSync(templatePath(name), "utf-8");
    cachedTemplates.set(name, text);
  }
  return text;
}

/** The remediation prompt template's text (Python's `bundled.remediation_template()`). */
export const remediationTemplate = (): string => template("remediation.md.tmpl");
/** The generated skill's SKILL.md template (Python's `bundled.skill_template()`). */
export const skillTemplate = (): string => template("skill.md.tmpl");
/** One finding note's template (Python's `bundled.skill_finding_template()`). */
export const skillFindingTemplate = (): string => template("skill-finding.md.tmpl");
/** REVERIFY.md's template (Python's `bundled.skill_reverify_template()`). */
export const skillReverifyTemplate = (): string => template("skill-reverify.md.tmpl");
