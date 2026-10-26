/**
 * The agentce-fix-gaps skill folder (SPEC §7, §13.3; Appendix A2 (C)): `SKILL.md`, one
 * `findings/<control>--<n>.md` note per finding, and `REVERIFY.md`.
 *
 * A faithful port of the Python reference's `render_skill_md`, `render_skill_finding_md`,
 * `render_reverify_md`, `_tool_names_for_subject` and `_skill_finding_filename` (`report.py`): fixed text
 * from the language-neutral templates (`spec/report/templates/skill*.md.tmpl`, vendored under
 * `data/templates/`) and only sanitised record- or catalog-derived strings, so every engine writes the
 * same bytes.
 */

import { skillFindingTemplate, skillReverifyTemplate, skillTemplate } from "./bundled";
import type { Event } from "./graph";
import { doubleQuoted } from "./pyyaml";
import { pyTruthy } from "./readiness";
import { sanitizeFinding } from "./remediation";
import { safe, sanitizeForMarkdown } from "./report";
import { render } from "./templating";
import { pyStr } from "./util";

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Every distinct tool name this subject's own ToolCall events name, sanitised, in first-seen order
 * (Python's `_tool_names_for_subject`). */
export function toolNamesForSubject(subjectEvents: Event[]): string[] {
  const names = new Set<string>();
  for (const event of subjectEvents) {
    const data = (event as { data?: unknown }).data;
    if (!isObject(data) || data["@type"] !== "ToolCall") continue;
    const tool = data.tool;
    const name = isObject(tool) ? tool.name : undefined;
    if (!pyTruthy(name)) continue;
    names.add(sanitizeForMarkdown(pyStr(name)));
  }
  return [...names];
}

/** One `findings/<control>--<n>.md` note from a remediation finding (Python's `render_skill_finding_md`). */
export function renderSkillFindingMd(finding: Json, toolCalls: string[]): string {
  return render(skillFindingTemplate(), {
    ...sanitizeFinding(finding),
    tool_calls: [...toolCalls],
  });
}

/** The generated skill's `SKILL.md` (Python's `render_skill_md`): the catalog-derived version lands in YAML
 * front matter, so it is sanitised and then written as a real double-quoted YAML scalar. */
export function renderSkillMd(
  specVersion: string,
  cliVersion: string,
  catalogVersion: string,
  assertionsDigest: string,
): string {
  return render(skillTemplate(), {
    spec_version: specVersion,
    cli_version: cliVersion,
    catalog_version: doubleQuoted(sanitizeForMarkdown(catalogVersion)),
    assertions_digest: assertionsDigest,
  });
}

/** `REVERIFY.md` for the full re-verify command line, program name included (Python's `render_reverify_md`). */
export function renderReverifyMd(argv: string[]): string {
  return render(skillReverifyTemplate(), { argv: argv.map((a) => sanitizeForMarkdown(a)) });
}

/** A finding note's file name (Python's `_skill_finding_filename`). */
export function skillFindingFilename(control: string, index: number): string {
  return `${safe(control)}--${index}.md`;
}
