/**
 * Render the report artifacts from assertions (SPEC §9).
 *
 * `assertions.json` is written in RFC 8785 canonical form (byte-identical across engines); the human
 * report (md/html), OSCAL Assessment Results, SARIF, and role-aware evidence packs are rendered from
 * it, and the reproducibility manifest records the digest of every input and output. DC-5 is enforced
 * before anything is written: a supporting verdict without an evidence pointer aborts the run. This is
 * a faithful port of the Python reference; the manifest is serialised as Python's
 * `json.dumps(sort_keys=True, indent=2)` so it is byte-for-byte identical.
 */

import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { arch, platform } from "node:os";
import { dirname, join } from "node:path";
import type { Activity } from "./activity";
import { DENIED_KINDS, RECORDER_CLASSES, summarizeActivity } from "./activity";
import { type Assertion, aggregate, assertionToJson, checkDc5 } from "./assertions";
import { indexBySubject } from "./assess";
import type { BlindSpot, BlindSpots, CheckRef } from "./blindSpots";
import { canonicalize } from "./canonical";
import type { Catalog, ControlSpec } from "./catalog";
import type { Event } from "./graph";
import { DEFAULT_LANGUAGE, catalogue } from "./messages";
import { type Profile, type Subject, profileFromDict } from "./profile";
import {
  type ProjectView,
  blindSpotsBySubject,
  computeProjectView,
  noPopulationBySubject,
} from "./project";
import { byteCompare, sortKeysDeep } from "./util";
import { gapText, summarize as summarizeVerdict } from "./verdict";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

const ZERO_DIGEST = `sha256:${"0".repeat(64)}`;
const NAMESPACE_URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";

/** Files left out of a catalog's provenance digest: the detached signature and `catalog.yaml`
 * itself (which carries the provenance block), so the digest covers the catalog's rules and is
 * non-circular (mirrors the Python reference's `catalog._PROVENANCE_EXCLUDE`). */
const PROVENANCE_EXCLUDE = new Set(["catalog.sig.json", "catalog.yaml"]);

/** Python's `Path.is_file()`: follows a symlink, and an unresolvable one is not a file. */
function isFileFollowingLinks(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/** Every file's POSIX relpath under `dir`, unsorted. */
function walkFiles(dir: string, base: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir).sort(byteCompare)) {
    const full = join(dir, name);
    const rel = base === "" ? name : `${base}/${name}`;
    // As Python's `rglob`: never descend a symlinked directory, follow a symlinked file, skip a
    // broken link.
    const entry = lstatSync(full);
    if (entry.isDirectory()) {
      out.push(...walkFiles(full, rel));
    } else if (entry.isFile() || (entry.isSymbolicLink() && isFileFollowingLinks(full))) {
      out.push(rel);
    }
  }
  return out;
}

/** Content-address a directory: `sha256:` over the sorted `<relpath>\0<filehash>` lines. Ports
 * the Python reference's `signing.digest_tree` exactly: sorted by the full POSIX relpath string
 * (code-point/byte order, so `a-b` sorts before `a/x`), `exclude` matched against the *full*
 * relpath (a nested `controls/catalog.yaml` is never excluded, only a top-level one), and any path
 * segment that is a dotfile or named `__pycache__` is skipped regardless of `exclude`. Recomputed
 * from the directory's real bytes every call -- never read from a catalog's stored
 * `provenance.digest` field. */
export function digestTree(dir: string, exclude: ReadonlySet<string> = new Set()): string {
  const rels = walkFiles(dir, "").sort(byteCompare);
  const lines: Buffer[] = [];
  for (const rel of rels) {
    if (exclude.has(rel)) {
      continue;
    }
    if (rel.split("/").some((part) => part.startsWith(".") || part === "__pycache__")) {
      continue;
    }
    const fileHash = createHash("sha256")
      .update(readFileSync(join(dir, rel)))
      .digest("hex");
    lines.push(Buffer.from(`${rel}\0${fileHash}`, "utf-8"));
  }
  const joined = Buffer.concat(
    lines.flatMap((line, i) => (i === 0 ? [line] : [Buffer.from("\n"), line])),
  );
  return `sha256:${createHash("sha256").update(joined).digest("hex")}`;
}

/** The catalog's real content digest (SPEC §14.5 CP-3): the same recomputation a reader can
 * independently verify against the catalog directory (mirrors the Python reference's
 * `catalog.catalog_provenance_digest`). */
export function catalogProvenanceDigest(directory: string): string {
  return digestTree(directory, PROVENANCE_EXCLUDE);
}

const SARIF_LEVEL: Record<string, string> = {
  "non-conformant": "error",
  partial: "warning",
  insufficient_evidence: "warning",
  not_assessed: "note",
};
const OSCAL_STATE: Record<string, string> = {
  conformant: "satisfied",
  "non-conformant": "not-satisfied",
  partial: "not-satisfied",
  not_applicable: "not-satisfied",
  not_assessed: "not-satisfied",
  insufficient_evidence: "not-satisfied",
};
/** An observation's OSCAL `methods` (SPEC.md:1166): `automated` -> TEST, `manual` -> EXAMINE; every
 * other mode (today, only `semi-automated`, and any unrecognised string -- `report --from` does not
 * validate `mode` against the catalog's enum, so a hand-edited assertions file can carry one) keeps
 * the base behaviour, `["TEST"]`. A `Map` (not a plain object) holds the lookup: a mode string that
 * names an `Object.prototype` member, e.g. `"constructor"`, has no entry in a `Map` and falls through
 * to the default, where plain `[a.mode]` indexing or `??` would instead resolve to that member's own
 * function, making `methods` a function and silently dropping the key from the JSON output --
 * schema-invalid, caught by this item's own critic review and pinned by a unit test below).
 * `semi-automated` is not raised to `["TEST", "EXAMINE"]` even though DC-6 (SPEC.md:246) defines the
 * mode as "automated evidence, human judgment": this engine does not yet consume a completed manual
 * checklist to confirm that judgment happened for any given assertion, so claiming `EXAMINE` for every
 * semi-automated finding would assert a human examination that, for an assertion with no such record,
 * did not happen. Ported field-for-field from the Python reference (`report.py`'s `_OSCAL_METHODS`);
 * see its comment for the full reasoning. */
const OSCAL_METHODS = new Map<string, string[]>([["manual", ["EXAMINE"]]]);

function digestBytes(data: Buffer): string {
  return `sha256:${createHash("sha256").update(data).digest("hex")}`;
}

function packageDigest(): string {
  return `sha256:${createHash("sha256").update(`${ENGINE_NAME}:${engineVersion()}`).digest("hex")}`;
}

/** RFC 4122 v5 UUID (SHA-1) in the URL namespace, matching Python's `uuid.uuid5`. */
function uuid5(...parts: string[]): string {
  const ns = Buffer.from(NAMESPACE_URL.replace(/-/g, ""), "hex");
  const name = Buffer.from(`agentce:${parts.join(":")}`, "utf-8");
  const hash = createHash("sha1").update(ns).update(name).digest().subarray(0, 16);
  const bytes = Buffer.from(hash);
  bytes[6] = ((bytes[6] as number) & 0x0f) | 0x50; // version 5
  bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80; // variant
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

function safe(name: string): string {
  return [...name].map((c) => (/[\p{L}\p{N}]/u.test(c) || "-._".includes(c) ? c : "_")).join("");
}

function bySubjectControl(a: Assertion, b: Assertion): number {
  return byteCompare(a.subject, b.subject) || byteCompare(a.control, b.control);
}

/** Escape a string for safe interpolation into HTML text or attribute content. */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

/** Cap applied when a record-derived string is sanitised for Markdown/terminal/HTML rendering (SPEC
 * §7 injection hardening): long enough to stay useful, short enough to bound a hostile payload. */
const SANITIZE_CAP = 200;

/** Shown in place of a name that neutralises to nothing: escaping, never erasure -- real activity
 * is never silently dropped to "0". */
const SANITIZE_EMPTY_NAME_PLACEHOLDER = "(unnamed)";

/** Shown in place of a subject id, evidence ref, or violation path that neutralises to nothing --
 * `SANITIZE_EMPTY_NAME_PLACEHOLDER`'s "(unnamed)" reads oddly for a field that was never a name. */
const SANITIZE_EMPTY_FIELD_PLACEHOLDER = "(empty)";

/** The complete, current Unicode `Default_Ignorable_Code_Point` property, as (first, last) inclusive
 * codepoint ranges -- hard-coded once and identical across all three engines, independent of any
 * engine's own Unicode database version (`\p{Cf}` alone misses variation selectors, CGJ, the
 * Mongolian free variation selectors, the Hangul fillers, and every reserved-for-future-use DICP
 * range). Mirrors `engines/python/agentce/report.py`'s `_DICP_RANGES` exactly. */
const DICP_RANGES: [number, number][] = [
  [0x00ad, 0x00ad],
  [0x034f, 0x034f],
  [0x061c, 0x061c],
  [0x115f, 0x1160],
  [0x17b4, 0x17b5],
  [0x180b, 0x180f],
  [0x200b, 0x200f],
  [0x202a, 0x202e],
  [0x2060, 0x206f],
  [0x3164, 0x3164],
  [0xfe00, 0xfe0f],
  [0xfeff, 0xfeff],
  [0xffa0, 0xffa0],
  [0xfff0, 0xfff8],
  [0x1bca0, 0x1bca3],
  [0x1d173, 0x1d17a],
  [0xe0000, 0xe0fff],
];

function isDicpCodepoint(codepoint: number): boolean {
  return DICP_RANGES.some(([lo, hi]) => codepoint >= lo && codepoint <= hi);
}

// Hoisted once (not re-literalised per codepoint inside the hot loops below): a fresh RegExp
// literal at each call site would otherwise allocate a new RegExp object per codepoint of every
// sanitised string across the corpus.
const RE_SPACE_LIKE = /\p{Cc}|\p{Co}|\p{Cs}|\p{Zl}|\p{Zp}/u;
const RE_FORMAT = /\p{Cf}/u;
const RE_ZS = /\p{Zs}/u;

/** Whether any codepoint in `text` is in the drop-set `neutralize` uses (category `Cf` or the
 * hard-coded `Default_Ignorable_Code_Point` table above). */
export function hasInvisibleCodepoint(text: string): boolean {
  return Array.from(text).some(
    (ch) => RE_FORMAT.test(ch) || isDicpCodepoint(ch.codePointAt(0) as number),
  );
}

/** The one shared core every sanitiser target calls first (SPEC §7 injection hardening, mirroring
 * the Python reference's `_neutralize`): replace every `Cc`/`Co`/`Cs`/`Zl`/`Zp` codepoint or `Zs`
 * (NBSP, ideographic space, ...) with a literal space, folding each run into one (one explicit,
 * per-engine-identical definition of "collapsible whitespace"); drop every `Cf`-or-
 * `Default_Ignorable_Code_Point` codepoint entirely (never a space -- removing a zero-width
 * character preserves the string's visual intent); trim leading and trailing whitespace; cap by
 * codepoint (never a UTF-16 half of a surrogate pair), computed here before any HTML-entity
 * expansion a caller applies on top; then render `placeholder` if the result is empty but `text`
 * was not. */
function neutralize(text: string, cap: number, placeholder: string): string {
  const kept: string[] = [];
  let inWs = false;
  for (const ch of text) {
    if (ch === " " || RE_SPACE_LIKE.test(ch) || RE_ZS.test(ch)) {
      if (!inWs) {
        kept.push(" ");
        inWs = true;
      }
    } else if (RE_FORMAT.test(ch) || isDicpCodepoint(ch.codePointAt(0) as number)) {
      // Dropped entirely (never a space): by definition invisible/zero-width.
    } else {
      kept.push(ch);
      inWs = false;
    }
  }
  let collapsed = kept.join("").trim();
  // A UTF-16 `.length` is always >= the codepoint count, so this is a safe, cheap sufficient
  // condition to skip the `Array.from` codepoint-array build below for the common (short,
  // uncapped) case.
  if (collapsed.length > cap) {
    const codepoints = Array.from(collapsed);
    if (codepoints.length > cap) {
      collapsed = `${codepoints.slice(0, cap - 1).join("")}…`;
    }
  }
  if (collapsed.length === 0 && text.length > 0) {
    collapsed = placeholder;
  }
  return collapsed;
}

/** The six substitutions `sanitizeForMarkdown`/`sanitizeForTerminal` apply on top of `neutralize`'s
 * output -- see `engines/python/agentce/report.py`'s `_MARKDOWN_SUBSTITUTIONS` for the full
 * rationale (breaks code-span/HTML/link/image/entity-reference syntax; deliberately not a broader
 * "fullwidth every punctuation character" rule). */
const MARKDOWN_SUBSTITUTIONS: [string, string][] = [
  ["`", "'"],
  ["<", "‹"],
  [">", "›"],
  ["[", "［"],
  ["]", "］"],
  ["&", "＆"],
];

/** Neutralise a record-derived string before it reaches `report.md`'s Markdown rendering or the
 * terminal (SPEC §7 injection hardening; mirrors the Python reference's `sanitize_for_markdown`). */
export function sanitizeForMarkdown(
  text: string,
  placeholder: string = SANITIZE_EMPTY_NAME_PLACEHOLDER,
  cap: number = SANITIZE_CAP,
): string {
  let neutralized = neutralize(text, cap, placeholder);
  for (const [oldCh, newCh] of MARKDOWN_SUBSTITUTIONS) {
    neutralized = neutralized.split(oldCh).join(newCh);
  }
  return neutralized;
}

/** A documented alias for `sanitizeForMarkdown`, not a second implementation -- see the Python
 * reference's own `sanitize_for_terminal` docstring for why. */
export const sanitizeForTerminal = sanitizeForMarkdown;

/** Neutralise a record-derived string for HTML rendering: `neutralize` first, then `escapeHtml` on
 * the result -- nothing else (mirrors the Python reference's `sanitize_for_html`). */
export function sanitizeForHtml(
  text: string,
  placeholder: string = SANITIZE_EMPTY_NAME_PLACEHOLDER,
  cap: number = SANITIZE_CAP,
): string {
  return escapeHtml(neutralize(text, cap, placeholder));
}

//: A self-contained stylesheet (no external references), mirroring the Python/Java renderers.
const HTML_STYLE =
  "body{font-family:system-ui,sans-serif;margin:2rem;color:#111;background:#fff;line-height:1.5}" +
  "h1{font-size:1.5rem}h2{font-size:1.2rem;margin-top:1.5rem}" +
  "table{border-collapse:collapse;width:100%}" +
  "th,td{border:1px solid #999;padding:.35rem .5rem;text-align:left}" +
  "th{background:#f0f0f0}";

/** `label 3, label 2` for every nonzero count, in the dict's (fixed) key order; `0` when every count
 * is zero -- shared by the Markdown, HTML, and terminal renderings. `labelOf` translates a key to its
 * catalogue label; a key with no translation (the effect classes, which are stable identifiers, not
 * prose) stands for itself. */
function activityTallyText(
  counts: Record<string, number>,
  labelOf?: Record<string, string>,
): string {
  const parts = Object.entries(counts)
    .filter(([, n]) => n)
    .map(([key, n]) => `${labelOf?.[key] ?? key} ${n}`);
  return parts.length > 0 ? parts.join(", ") : "0";
}

function activityUndeclaredLines(
  undeclared: { tools: string[]; models: string[]; agents: string[] },
  cat: Record<string, string>,
): string[] {
  if (
    undeclared.tools.length === 0 &&
    undeclared.models.length === 0 &&
    undeclared.agents.length === 0
  ) {
    return [cat["report.activity_none_undeclared"] as string];
  }
  const lines: string[] = [];
  if (undeclared.agents.length > 0) {
    lines.push(
      `${cat["report.activity_undeclared_agents_label"]}: ` +
        `${undeclared.agents.map((name) => sanitizeForMarkdown(name)).join(", ")}`,
    );
  }
  if (undeclared.tools.length > 0) {
    lines.push(
      `${cat["report.activity_undeclared_tools_label"]}: ` +
        `${undeclared.tools.map((name) => sanitizeForMarkdown(name)).join(", ")}`,
    );
  }
  if (undeclared.models.length > 0) {
    lines.push(
      `${cat["report.activity_undeclared_models_label"]}: ` +
        `${undeclared.models.map((name) => sanitizeForMarkdown(name)).join(", ")}`,
    );
  }
  return lines;
}

/** `(label, value)` for every counted-facts row -- the one place the row set and order is decided,
 * shared by the Markdown, HTML, and terminal renderings. Agent, model, and tool names are
 * event-derived strings (SPEC §7 injection hardening), escaped with `sanitizeForMarkdown` before joining so a
 * hostile name (embedded newlines) can never start a new Markdown/terminal line -- this section
 * renders before the verdict. */
function activityRows(activity: Activity, cat: Record<string, string>): [string, string][] {
  const recorderLabels: Record<string, string> = {};
  for (const k of RECORDER_CLASSES)
    recorderLabels[k] = cat[`report.activity_recorder_${k}`] as string;
  const deniedLabels: Record<string, string> = {};
  for (const k of DENIED_KINDS) deniedLabels[k] = cat[`report.activity_denied_${k}`] as string;
  return [
    [
      cat["report.activity_agents_label"] as string,
      activity.agents.length > 0
        ? activity.agents.map((a) => sanitizeForMarkdown(a)).join(", ")
        : (cat["report.activity_none_agents"] as string),
    ],
    [
      cat["report.activity_models_label"] as string,
      activity.models.map((m) => sanitizeForMarkdown(m.name)).join(", ") || "0",
    ],
    [
      cat["report.activity_tools_label"] as string,
      activity.tools.map((t) => sanitizeForMarkdown(t.name)).join(", ") || "0",
    ],
    [
      cat["report.activity_actions_label"] as string,
      activityTallyText(activity.actions_by_effect_class),
    ],
    [
      cat["report.activity_approvals_label"] as string,
      activityTallyText(activity.approvals_by_recorder, recorderLabels),
    ],
    [
      cat["report.activity_denied_label"] as string,
      activityTallyText(activity.denied_or_blocked, deniedLabels),
    ],
  ];
}

/** The lines that lead the report body (before the verdict, SPEC's evidence-first framing): what the
 * records show your agents did, regardless of how they measure up. */
function activityMd(activity: Activity, cat: Record<string, string>): string[] {
  const lines = [`## ${cat["report.activity_heading"]}`, ""];
  for (const [label, value] of activityRows(activity, cat)) lines.push(`- ${label}: ${value}`);
  lines.push("", `### ${cat["report.activity_undeclared_heading"]}`, "");
  for (const line of activityUndeclaredLines(activity.undeclared, cat)) lines.push(`- ${line}`);
  lines.push("");
  return lines;
}

function activityHtml(activity: Activity, cat: Record<string, string>): string {
  const items = activityRows(activity, cat)
    .map(([label, value]) => `<li>${escapeHtml(label)}: ${escapeHtml(value)}</li>`)
    .join("");
  const undeclared = activityUndeclaredLines(activity.undeclared, cat)
    .map((line) => `<p>${escapeHtml(line)}</p>`)
    .join("");
  return `<section aria-labelledby="activity"><h2 id="activity">${escapeHtml(cat["report.activity_heading"] as string)}</h2><ul>${items}</ul><h3>${escapeHtml(cat["report.activity_undeclared_heading"] as string)}</h3>${undeclared}</section>`;
}

/** The lines a command prints for `activity`: agents, tools, models, and anything not yet declared --
 * the same dictionary `activityMd`/`activityHtml` render. */
export function activityCliLines(activity: Activity, catalogue: Record<string, string>): string[] {
  const lines = activityRows(activity, catalogue).map(([label, value]) => `${label}: ${value}`);
  lines.push(`${catalogue["report.activity_undeclared_heading"]}:`);
  for (const line of activityUndeclaredLines(activity.undeclared, catalogue)) {
    lines.push(`  ${line}`);
  }
  return lines;
}

const OWNER_LABEL: Record<string, string> = {
  agent_team: "the agent team",
  platform_or_security: "platform or security",
  ticketing_or_iam: "whoever runs ticketing or IAM",
};

/** No English string is stored in `blind-spots.json` itself (RFC 0008 Sec.7): the artifact carries
 * only `owner_key`/`step_kind` tokens, and only the rendered report resolves them to text. Unlike
 * the Python engine, this text is never routed through the message catalogue (RFC 0008 Sec.7: "the
 * pre-existing, accepted scope boundary that rendered report.md/report.html output has never been a
 * three-engine byte-identity requirement"). */
function blindSpotStepText(stepKind: string, ownerLabel: string): string {
  return stepKind === "request"
    ? `a request to ${ownerLabel}`
    : `a code change for ${ownerLabel} (see the agentce-get-evidence skill)`;
}

/** `sanitizeForMarkdown` with the field placeholder rather than the name placeholder (mirrors the
 * Python reference's `_sanitize_field`): `event`/`class` come from the catalog, but a
 * `no_population` entry's `subject` can be records-derived, and the section renders right after
 * activity and before the verdict (RFC 0008 Sec.7) -- the same position P11 (item 18.4 rework)
 * forged a fake verdict line through. */
function sanitizeField(value: string): string {
  return sanitizeForMarkdown(value, SANITIZE_EMPTY_FIELD_PLACEHOLDER);
}

/** `(label, value)` for every blind spot, in the module's own ranked order (never re-sorted here). */
function blindSpotRows(blindSpots: BlindSpot[]): [string, string][] {
  return blindSpots.map((bs) => {
    const ownerLabel = OWNER_LABEL[bs.owner_key] as string;
    const step = blindSpotStepText(bs.step_kind, ownerLabel);
    const adapters =
      bs.supplying_adapters.map((a) => sanitizeField(a)).join(", ") || "no adapter today";
    const value =
      `unlocks ${bs.checks_unlocked} check(s), needed by ${bs.needed_by} more; ` +
      `rung ${bs.ladder_rung} -- ${step}. Adapters that can supply this: ${adapters}.`;
    return [`${sanitizeField(bs.event)} (${sanitizeField(bs.class)})`, value];
  });
}

function noPopulationRows(noPopulation: CheckRef[]): [string, string][] {
  return noPopulation.map((entry) => [
    `${sanitizeField(entry.control)} on ${sanitizeField(entry.subject)} ` +
      `(${sanitizeField(entry.catalog)}@${sanitizeField(entry.control_version)})`,
    `The records show every kind of evidence ${sanitizeField(entry.control)} asks for, but not enough of it in the shape the control expects -- a --domain binding may be needed to identify the relevant decisions; see the control's documentation for what it needs.`,
  ]);
}

function blindSpotsMd(blindSpots: BlindSpots): string[] {
  const rows = blindSpotRows(blindSpots.blind_spots);
  const noPopRows = noPopulationRows(blindSpots.no_population);
  const lines = ["## Where your records can't show it yet", ""];
  if (rows.length === 0 && noPopRows.length === 0) {
    lines.push("- every check either has enough evidence, or nothing here would unlock more", "");
    return lines;
  }
  // The label is backtick-wrapped, not just interpolated after the list marker: a sanitised value
  // alone can still start with `#`/`~~~`/a digit-`.` sequence CommonMark parses as a heading, code
  // fence, or nested list when it is the first token of a list item's content. A single leading
  // backtick (matching the assertions table's existing `- \`{control}\` @ ...` safe shape) means the
  // payload's own leading character is never the line's first content, and is always the only
  // backtick on the line since sanitizeForMarkdown already substitutes any embedded backtick.
  for (const [label, value] of rows) lines.push(`- \`${label}\`: ${value}`);
  if (noPopRows.length > 0) {
    lines.push("", "### Records that don't show enough, with no single fix", "");
    for (const [label, value] of noPopRows) lines.push(`- \`${label}\`: ${value}`);
  }
  lines.push("");
  return lines;
}

function blindSpotsHtml(blindSpots: BlindSpots): string {
  const rows = blindSpotRows(blindSpots.blind_spots);
  const noPopRows = noPopulationRows(blindSpots.no_population);
  let body: string;
  if (rows.length === 0 && noPopRows.length === 0) {
    body = "<p>every check either has enough evidence, or nothing here would unlock more</p>";
  } else {
    const items = (list: [string, string][]): string =>
      list
        .map(
          ([label, value]) =>
            `<li><strong>${escapeHtml(label)}</strong>: ${escapeHtml(value)}</li>`,
        )
        .join("");
    body = `<ul>${items(rows)}</ul>`;
    if (noPopRows.length > 0) {
      body += `<h3>Records that don't show enough, with no single fix</h3><ul>${items(noPopRows)}</ul>`;
    }
  }
  return `<section aria-labelledby="blind-spots"><h2 id="blind-spots">Where your records can't show it yet</h2>${body}</section>`;
}

/** The lines a command prints for `blindSpots`: the same dictionary `blindSpotsMd`/`blindSpotsHtml`
 * render. */
export function blindSpotsCliLines(blindSpots: BlindSpots): string[] {
  const rows = blindSpotRows(blindSpots.blind_spots);
  const noPopRows = noPopulationRows(blindSpots.no_population);
  const lines = ["Where your records can't show it yet:"];
  if (rows.length === 0 && noPopRows.length === 0) {
    lines.push("  every check either has enough evidence, or nothing here would unlock more");
    return lines;
  }
  for (const [label, value] of rows) lines.push(`  ${label}: ${value}`);
  if (noPopRows.length > 0) {
    lines.push("  Records that don't show enough, with no single fix:");
    for (const [label, value] of noPopRows) lines.push(`    ${label}: ${value}`);
  }
  return lines;
}

/** `cat["outcome.<key>"]` falling back to the raw key (mirrors the Python/Java engines'
 * `_outcome_label`/`outcomeLabel`), so the report shows the human label (`insufficient evidence`)
 * instead of the raw enum (`insufficient_evidence`). */
function outcomeLabel(cat: Record<string, string>, outcome: string): string {
  return cat[`outcome.${outcome}`] ?? outcome;
}

type VerdictSummary = ReturnType<typeof summarizeVerdict>;

/** The lines that lead the report: the verdict, the top gaps, and the next step (SPEC §9.2).
 * Mirrors the Python reference's `_verdict_md` and Java's `renderReportMd` verdict block exactly,
 * content for content. */
function verdictMd(summary: VerdictSummary, cat: Record<string, string>): string[] {
  const state = summary.verdict;
  const lines = [`## ${cat["report.verdict_heading"]}`, "", `**${cat[`verdict.${state}`]}**`, ""];
  if (summary.topGaps.length > 0) {
    lines.push(`${cat["report.top_gaps_heading"]}:`, "");
    for (const gap of summary.topGaps) {
      lines.push(`- ${gapText(gap, cat)}`);
    }
  } else {
    lines.push(`${cat["report.top_gaps_heading"]}: ${cat["report.no_gaps"]}`);
  }
  lines.push("", `${cat["report.next_step_heading"]}: ${cat[`next.${state}`]}`, "");
  return lines;
}

function verdictHtml(summary: VerdictSummary, cat: Record<string, string>): string {
  const state = summary.verdict;
  const heading = escapeHtml(cat["report.top_gaps_heading"] as string);
  let gaps: string;
  if (summary.topGaps.length > 0) {
    const items = summary.topGaps
      .map((gap) => `<li>${escapeHtml(gapText(gap, cat))}</li>`)
      .join("");
    gaps = `<p>${heading}:</p><ul>${items}</ul>`;
  } else {
    gaps = `<p>${heading}: ${escapeHtml(cat["report.no_gaps"] as string)}</p>`;
  }
  return (
    `<section aria-labelledby="verdict"><h2 id="verdict">${escapeHtml(cat["report.verdict_heading"] as string)}</h2>` +
    `<p><strong>${escapeHtml(cat[`verdict.${state}`] as string)}</strong></p>${gaps}` +
    `<p>${escapeHtml(cat["report.next_step_heading"] as string)}: ${escapeHtml(cat[`next.${state}`] as string)}</p></section>`
  );
}

export function renderReportMd(
  assertions: Assertion[],
  counts: Record<string, number>,
  language: string = DEFAULT_LANGUAGE,
  activity?: Activity,
  blindSpots?: BlindSpots,
): string {
  const cat = catalogue(language);
  const lines = [`# ${cat["report.title"]}`, ""];
  // The records lead the report (SPEC's evidence-first framing, 18.4): what happened, before how it
  // measures up. What the records can't show yet (18.5) comes right after.
  if (activity !== undefined) {
    lines.push(...activityMd(activity, cat));
  }
  if (blindSpots !== undefined) {
    lines.push(...blindSpotsMd(blindSpots));
  }
  lines.push(...verdictMd(summarizeVerdict(assertions), cat));
  lines.push(`## ${cat["report.summary_heading"]}`, "");
  for (const [outcome, count] of Object.entries(counts)) {
    // List-item first content: backtick-wrapped (SPEC §7 injection hardening;
    // `contracts/P18-18.21.md`), matching the Python/Java engines' summary tally.
    lines.push(`- \`${sanitizeForMarkdown(outcomeLabel(cat, outcome))}\`: ${count}`);
  }
  lines.push("", `## ${cat["report.assertions_heading"]}`, "");
  if (assertions.length === 0) {
    lines.push(`_${cat["report.no_controls"]}_`);
  }
  for (const a of [...assertions].sort(bySubjectControl)) {
    const control = sanitizeForMarkdown(a.control);
    const subject = sanitizeForMarkdown(a.subject);
    lines.push(
      `- \`${control}\` @ \`${subject}\` -> **${sanitizeForMarkdown(outcomeLabel(cat, a.outcome))}** ` +
        `(rung ${a.rung}, ${sanitizeForMarkdown(a.mode)}; ` +
        `${a.population[1]}/${a.population[0]} failed)`,
    );
  }
  return `${lines.join("\n")}\n`;
}

/** Render a self-contained, escaped, WCAG 2.2 AA report page (SPEC §9.3): a strict CSP meta tag, no
 * external references, one `h1`, a `main` landmark, and every catalog- or evidence-derived string
 * rendered as escaped text, never as markup. */
export function renderReportHtml(
  assertions: Assertion[],
  counts: Record<string, number>,
  language: string = DEFAULT_LANGUAGE,
  activity?: Activity,
  blindSpots?: BlindSpots,
): string {
  const cat = catalogue(language);
  const title = escapeHtml(cat["report.title"] as string);
  const summary = Object.entries(counts)
    .map(([o, c]) => `<li>${sanitizeForHtml(outcomeLabel(cat, o))}: ${c}</li>`)
    .join("");
  const rows = [...assertions]
    .sort(bySubjectControl)
    .map(
      (a) =>
        `<tr><td>${sanitizeForHtml(a.control)}</td><td>${sanitizeForHtml(a.subject)}</td>` +
        `<td>${sanitizeForHtml(outcomeLabel(cat, a.outcome))}</td></tr>`,
    )
    .join("");
  const bodyRows =
    rows || `<tr><td colspan="3">${escapeHtml(cat["report.no_controls"] as string)}</td></tr>`;
  const csp = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'";
  const activitySection = activity !== undefined ? activityHtml(activity, cat) : "";
  const blindSpotsSection = blindSpots !== undefined ? blindSpotsHtml(blindSpots) : "";
  const verdictSection = verdictHtml(summarizeVerdict(assertions), cat);
  return `<!doctype html><html lang="${escapeHtml(language)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${csp}"><title>${title}</title><style>${HTML_STYLE}</style></head><body><main><h1>${title}</h1>${activitySection}${blindSpotsSection}${verdictSection}<section aria-labelledby="summary"><h2 id="summary">${escapeHtml(cat["report.summary_heading"] as string)}</h2><ul>${summary}</ul></section><section aria-labelledby="assertions"><h2 id="assertions">${escapeHtml(cat["report.assertions_heading"] as string)}</h2><table><tr><th>Control</th><th>Subject</th><th>Outcome</th></tr>${bodyRows}</table></section></main></body></html>\n`;
}

/** The traversal-proof, collision-resistant directory name `writeReport` writes a subject's own
 * report under (`agents/<dirname>/`, 18.14 C3) -- the one formula both `writeReport` (which creates
 * the directory) and `projectAgentViewRows`/the undeclared-agents section (which link to it) share,
 * mirroring the Python reference's `_agent_dirname`. */
function agentDirname(subjectId: string): string {
  return `${safe(subjectId).slice(0, 40)}-${uuid5(subjectId).slice(0, 8)}`;
}

/** Minimal named-placeholder substitution (`{name}` -> value) for the two project-view message
 * templates that need it -- `formatPlural` (verdict.ts) already covers the catalogue's one ICU
 * plural template, so this is deliberately narrower than a general formatter (mirrors the Python
 * reference's `i18n_format.format_message` for this non-plural case). */
function formatTemplate(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => vars[key] ?? whole);
}

interface ProjectAgentDisplayRow {
  id: string;
  dirname: string;
  badge: string;
  verdict: string;
  whatItDid: string;
  deviations: string;
}

/** One row per agent, in `projectView.agents`'s own order (never re-sorted here): the sanitised id,
 * its report's directory name, the declared/undeclared badge, the verdict-and-counts cell, and the
 * what-it-did cell -- shared by the Markdown and HTML renderings exactly like `activityRows`. */
function projectAgentViewRows(
  projectView: ProjectView,
  activityBySubject: Map<string, Activity>,
  cat: Record<string, string>,
): ProjectAgentDisplayRow[] {
  return projectView.agents.map((agent) => {
    const activity = activityBySubject.get(agent.id);
    const counts = agent.summary.counts;
    const labelOf: Record<string, string> = {};
    for (const outcome of Object.keys(counts)) labelOf[outcome] = outcomeLabel(cat, outcome);
    const verdictLabel = cat[`verdict.${agent.summary.verdict}`] as string;
    const agentsObserved =
      (activity?.agents ?? []).map((a) => sanitizeField(a)).join(", ") ||
      (cat["report.activity_none_agents"] as string);
    return {
      id: sanitizeField(agent.id),
      dirname: agentDirname(agent.id),
      badge: agent.declared
        ? (cat["report.project_declared_badge"] as string)
        : (cat["report.project_undeclared_badge"] as string),
      verdict: `${verdictLabel} (${activityTallyText(counts, labelOf)})`,
      whatItDid: formatTemplate(cat["report.project_what_it_did_cell"] as string, {
        agents: agentsObserved,
        actions: activityTallyText(activity?.actions_by_effect_class ?? {}),
      }),
      deviations:
        agent.deviations
          .map((d) =>
            d.expiry === undefined
              ? formatTemplate(cat["report.project_deviation_entry"] as string, {
                  control: sanitizeField(d.control),
                })
              : formatTemplate(cat["report.project_deviation_entry_with_expiry"] as string, {
                  control: sanitizeField(d.control),
                  expiry: sanitizeField(d.expiry),
                }),
          )
          .join(", ") || (cat["report.project_deviations_none"] as string),
    };
  });
}

/** As `blindSpotRows`, but each row also names the agents this gap touches (`computeProjectView`'s
 * own `top_gaps`, C2) -- the project view's per-gap agent list, not a per-agent re-scoping. */
function projectTopGapRows(
  topGaps: ProjectView["top_gaps"],
  cat: Record<string, string>,
): [string, string][] {
  return blindSpotRows(topGaps).map(([label, value], i) => {
    const agents = topGaps[i]?.agents ?? [];
    const suffix = formatTemplate(cat["report.project_top_gap_agents"] as string, {
      agents: agents.map((a) => sanitizeField(a)).join(", "),
    });
    return [label, `${value} ${suffix}`];
  });
}

/** The project view (Hill 7, 18.14 C3): every agent this run assessed, side by side -- a summary row
 * per agent (declared or discovered), the top gaps across the whole project naming which agents
 * each touches, then the agents nobody declared, each linking to its own full report under
 * `agents/<dirname>/`. */
export function renderProjectMd(
  projectView: ProjectView,
  activityBySubject: Map<string, Activity>,
  language: string = DEFAULT_LANGUAGE,
): string {
  const cat = catalogue(language);
  const rows = projectAgentViewRows(projectView, activityBySubject, cat);
  const lines = [
    `# ${cat["report.project_title"]}`,
    "",
    `## ${cat["report.project_heading"]}`,
    "",
    `| ${cat["report.project_agent_column"]} | ${cat["report.project_declared_column"]} | ` +
      `${cat["report.project_verdict_column"]} | ${cat["report.project_what_it_did_column"]} | ` +
      `${cat["report.project_deviations_column"]} |`,
    "|---|---|---|---|---|",
  ];
  for (const row of rows) {
    lines.push(
      `| [${row.id}](agents/${row.dirname}/report.md) | ${row.badge} | ` +
        `${row.verdict} | ${row.whatItDid} | ${row.deviations} |`,
    );
  }
  lines.push("", `## ${cat["report.project_top_gaps_heading"]}`, "");
  const gapRows = projectTopGapRows(projectView.top_gaps, cat);
  if (gapRows.length > 0) {
    for (const [label, value] of gapRows) lines.push(`- \`${label}\`: ${value}`);
  } else {
    lines.push(`- ${cat["report.no_gaps"]}`);
  }
  if (projectView.undeclared_agents.length > 0) {
    lines.push("", `## ${cat["report.project_undeclared_heading"]}`, "");
    for (const agentId of projectView.undeclared_agents) {
      lines.push(
        `- \`${sanitizeField(agentId)}\` -- [${cat["report.project_agent_report_link"]}]` +
          `(agents/${agentDirname(agentId)}/report.md)`,
      );
    }
  }
  return `${lines.join("\n")}\n`;
}

/** As `renderProjectMd`, rendered as the same self-contained, escaped, WCAG 2.2 AA page shape as
 * `renderReportHtml`. */
export function renderProjectHtml(
  projectView: ProjectView,
  activityBySubject: Map<string, Activity>,
  language: string = DEFAULT_LANGUAGE,
): string {
  const cat = catalogue(language);
  const rows = projectAgentViewRows(projectView, activityBySubject, cat);
  const title = escapeHtml(cat["report.project_title"] as string);
  const bodyRows = rows
    .map(
      (row) =>
        `<tr><td><a href="agents/${row.dirname}/report.html">${escapeHtml(row.id)}</a></td>` +
        `<td>${escapeHtml(row.badge)}</td><td>${escapeHtml(row.verdict)}</td>` +
        `<td>${escapeHtml(row.whatItDid)}</td><td>${escapeHtml(row.deviations)}</td></tr>`,
    )
    .join("");
  const gapRows = projectTopGapRows(projectView.top_gaps, cat);
  const gapsHtml =
    gapRows.length > 0
      ? `<ul>${gapRows
          .map(
            ([label, value]) =>
              `<li><strong>${escapeHtml(label)}</strong>: ${escapeHtml(value)}</li>`,
          )
          .join("")}</ul>`
      : `<p>${escapeHtml(cat["report.no_gaps"] as string)}</p>`;
  let undeclaredHtml = "";
  if (projectView.undeclared_agents.length > 0) {
    const items = projectView.undeclared_agents
      .map(
        (agentId) =>
          `<li>${sanitizeForHtml(agentId)} -- ` +
          `<a href="agents/${agentDirname(agentId)}/report.html">` +
          `${escapeHtml(cat["report.project_agent_report_link"] as string)}</a></li>`,
      )
      .join("");
    undeclaredHtml = `<section aria-labelledby="project-undeclared"><h2 id="project-undeclared">${escapeHtml(cat["report.project_undeclared_heading"] as string)}</h2><ul>${items}</ul></section>`;
  }
  const csp = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'";
  return `<!doctype html><html lang="${escapeHtml(language)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${csp}"><title>${title}</title><style>${HTML_STYLE}</style></head><body><main><h1>${title}</h1><section aria-labelledby="project-agents"><h2 id="project-agents">${escapeHtml(cat["report.project_heading"] as string)}</h2><table><tr><th>${escapeHtml(cat["report.project_agent_column"] as string)}</th><th>${escapeHtml(cat["report.project_declared_column"] as string)}</th><th>${escapeHtml(cat["report.project_verdict_column"] as string)}</th><th>${escapeHtml(cat["report.project_what_it_did_column"] as string)}</th><th>${escapeHtml(cat["report.project_deviations_column"] as string)}</th></tr>${bodyRows}</table></section><section aria-labelledby="project-top-gaps"><h2 id="project-top-gaps">${escapeHtml(cat["report.project_top_gaps_heading"] as string)}</h2>${gapsHtml}</section>${undeclaredHtml}</main></body></html>\n`;
}

/** A deterministic OSCAL date-time for this run: the earliest evidence-window start across the
 * assertions, so it reflects what was actually reviewed rather than the run's wall clock. A run
 * with no assertions falls back to a fixed epoch, never `now()`. */
function oscalTimestamp(assertions: Assertion[]): string {
  if (assertions.length === 0) return "1970-01-01T00:00:00Z";
  return assertions.map((a) => a.window[0]).sort(byteCompare)[0] as string;
}

/** Render `oscal-ar.json` as an importable NIST OSCAL 1.1.2 Assessment Results document (SPEC §9,
 * §9.4): every result carries real `observations[]` built from the assertion's own evidence
 * pointers, every finding resolves to the observation that backs it and links to its real control id
 * so a GRC platform can trace the finding to the requirement it assesses. A faithful port of the
 * Python reference; no catalog object is needed since the control id alone is the traceable token. */
export function renderOscal(assertions: Assertion[]): Record<string, unknown> {
  const ordered = [...assertions].sort(bySubjectControl);
  const when = oscalTimestamp(assertions);

  const observationUuid = new Map<string, string>();
  const observations: Record<string, unknown>[] = [];
  for (const a of ordered) {
    const key = `${a.control}\0${a.subject}`;
    const obsUuid = uuid5("observation", a.control, a.subject);
    observationUuid.set(key, obsUuid);
    const observation: Record<string, unknown> = {
      uuid: obsUuid,
      description: `Assessment activity for ${a.control} on ${a.subject}.`,
      methods: OSCAL_METHODS.get(a.mode) ?? ["TEST"],
      collected: when,
    };
    if (a.evidence.length > 0) {
      observation["relevant-evidence"] = a.evidence.map((e) => ({
        href: e.ref,
        description: `${e.sourceClass} evidence, digest ${e.digest}`,
      }));
    }
    observations.push(observation);
  }

  const findings = ordered.map((a) => {
    const key = `${a.control}\0${a.subject}`;
    return {
      uuid: uuid5("finding", a.control, a.subject),
      title: `${a.control} for ${a.subject}`,
      description: `${a.control} assessed for ${a.subject}: ${a.outcome}.`,
      target: {
        type: "objective-id",
        "target-id": a.control,
        status: { state: OSCAL_STATE[a.outcome] ?? "not-satisfied", reason: a.outcome },
      },
      links: [{ href: `urn:agentce:control:${a.control}`, rel: "control" }],
      "related-observations": [{ "observation-uuid": observationUuid.get(key) }],
    };
  });

  const result: Record<string, unknown> = {
    uuid: uuid5("result"),
    title: "AgentCE structural assessment",
    description:
      "AgentCE's structural, statistical, and probe-based assessment of the run's subjects " +
      "against the resolved catalog(s).",
    start: when,
    "reviewed-controls": { "control-selections": [{ "include-all": {} }] },
  };
  if (observations.length > 0) result.observations = observations;
  if (findings.length > 0) result.findings = findings;

  return {
    "assessment-results": {
      uuid: uuid5("assessment-results"),
      metadata: {
        title: "AgentCE Assessment Results",
        version: engineVersion(),
        "oscal-version": "1.1.2",
        "last-modified": when,
      },
      "import-ap": { href: "urn:agentce:assessment-plan:structural" },
      results: [result],
    },
  };
}

function controlIndex(catalogs: Catalog[]): Map<string, ControlSpec> {
  const index = new Map<string, ControlSpec>();
  for (const catalog of catalogs) {
    for (const control of catalog.controls) {
      index.set(control.id, control);
    }
  }
  return index;
}

/** A stable, deterministic reference URL for the control's family page (`docs/reference/catalog/`,
 * published at the site under the same path). SARIF's `helpUri` is a citation, not a runtime
 * dependency: it does not need to resolve for the check that reads it, the same way a JSON Schema
 * `$id` does not (`spec/report/vendor/README.md`). */
function sarifHelpUri(control: string): string {
  const family = control.split("-", 1)[0];
  return `https://agent-conformance.org/reference/catalog/${family}`;
}

function sarifRuleHelpText(control: string, spec: ControlSpec | undefined): string {
  return spec !== undefined ? spec.title : `AgentCE control ${control}.`;
}

/** A fingerprint derived only from the assertion's own content -- control, subject, outcome, and the
 * evaluation window/population that produced it -- so two independent offline runs over the same
 * evidence produce byte-identical fingerprints (no clock, host, or run counter). */
function sarifFingerprint(a: Assertion): string {
  const payload = [
    a.control,
    a.subject,
    a.outcome,
    a.window[0],
    a.window[1],
    String(a.population[0]),
    String(a.population[1]),
  ].join("|");
  return createHash("sha256").update(payload, "utf-8").digest("hex");
}

/** Render `results.sarif` (SPEC §9) as a document a code-scanning consumer can actually use: every
 * rule carries catalog-sourced `name`/`help`/`helpUri`, every result carries a synthetic `locations`
 * entry (the subject is not a source file, so the location is a stable pseudo-path for that subject)
 * and a content-derived `partialFingerprints` (stable across independent runs, so findings de-dup
 * across scans), and `not_assessed` surfaces at a level distinct from `insufficient_evidence` so a
 * reader -- and a code-scanning gate -- can tell unproven apart from thin evidence instead of one
 * being silent. */
export function renderSarif(
  assertions: Assertion[],
  catalogs: Catalog[] = [],
): Record<string, unknown> {
  const byControl = controlIndex(catalogs);
  const controls = [...new Set(assertions.map((a) => a.control))].sort(byteCompare);
  const rules = controls.map((control) => ({
    id: control,
    name: control,
    help: { text: sarifRuleHelpText(control, byControl.get(control)) },
    helpUri: sarifHelpUri(control),
  }));
  const results = [...assertions]
    .sort(bySubjectControl)
    .filter((a) => a.outcome in SARIF_LEVEL)
    .map((a) => ({
      ruleId: a.control,
      level: SARIF_LEVEL[a.outcome],
      message: { text: `${a.control} on ${a.subject}: ${a.outcome}` },
      locations: [
        { physicalLocation: { artifactLocation: { uri: `agentce/subjects/${safe(a.subject)}` } } },
      ],
      partialFingerprints: { "agentceOutcomeHash/v1": sarifFingerprint(a) },
    }));
  return {
    $schema: "https://agent-conformance.org/spec/report/results-sarif.schema.json",
    version: "2.1.0",
    runs: [{ tool: { driver: { name: ENGINE_NAME, version: engineVersion(), rules } }, results }],
  };
}

export function renderEvidencePack(
  subject: string,
  assertions: Assertion[],
): Record<string, unknown> {
  const refs = new Set<string>();
  for (const a of assertions) {
    for (const e of a.evidence) {
      refs.add(e.ref);
    }
  }
  return {
    subject,
    assertions: assertions.map((a) => ({
      control: a.control,
      outcome: a.outcome,
      mode: a.mode,
      evidence: [...new Set(a.evidence.map((e) => e.ref))].sort(byteCompare),
      ...(a.crosswalk.length > 0 ? { crosswalk: a.crosswalk } : {}),
    })),
    evidence: [...refs].sort(byteCompare),
  };
}

function now(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export interface ManifestOptions {
  bundleDigest: string;
  catalogs: string[];
  /** The resolved catalog objects, matched to `catalogs` by `id@version`, so each ref's digest is
   * the catalog directory's real, recomputed-every-call content digest (SPEC §14.5 CP-3) --
   * never read from a catalog's stored `provenance.digest`. A label with no matching object here
   * (a bare re-render that has only labels, no directories) keeps the honest all-zero digest. */
  catalogObjects?: Catalog[];
  outputs: Record<string, string>;
  operator: string;
  invocation: string[];
  supersedes: string[];
  reportLanguage?: string;
  /** What `--allow-unverified-catalog` waived (SPEC §8.7): absent from an ordinary run's manifest,
   * never an empty array (18.36). */
  limitations?: string[];
}

export function buildManifest(options: ManifestOptions): Record<string, unknown> {
  const pkgDigest = packageDigest();
  const host = createHash("sha256").update(`${platform()}|${arch()}|${pkgDigest}`).digest("hex");
  const byLabel = new Map((options.catalogObjects ?? []).map((c) => [`${c.id}@${c.version}`, c]));
  const catalogRefs = options.catalogs.map((entry) => {
    const at = entry.indexOf("@");
    const cid = at >= 0 ? entry.slice(0, at) : entry;
    const version = at >= 0 ? entry.slice(at + 1) : "";
    const catalog = byLabel.get(entry);
    const digest = catalog !== undefined ? catalogProvenanceDigest(catalog.directory) : ZERO_DIGEST;
    return { id: cid, version: version || "0", digest };
  });
  const manifest: Record<string, unknown> = {
    agentce_manifest_version: 1,
    engine: {
      impl: ENGINE_NAME,
      version: engineVersion(),
      spec_version: SPEC_VERSION,
      package_digest: pkgDigest,
    },
    inputs: { bundle_digest: options.bundleDigest, catalogs: catalogRefs },
    outputs: options.outputs,
    run: {
      started_at: now(),
      operator: options.operator,
      host_fingerprint: `sha256:${host}`,
      invocation: options.invocation,
      report_language: options.reportLanguage ?? DEFAULT_LANGUAGE,
    },
  };
  if (options.supersedes.length > 0) {
    manifest.supersedes = options.supersedes;
  }
  if (options.limitations?.length) {
    manifest.limitations = options.limitations;
  }
  return manifest;
}

export interface WriteReportOptions {
  bundleDigest: string;
  catalogs: string[];
  /** The resolved catalog objects (not just their `id@version` labels), so `results.sarif` can carry
   * catalog-sourced rule metadata (SPEC §9). Optional so a caller with only labels (a bare re-render)
   * still gets a valid, if less informative, SARIF document. */
  catalogObjects?: Catalog[];
  operator?: string;
  invocation?: string[];
  supersedes?: string[];
  reportLanguage?: string;
  /** `summarizeActivity` over the run's accepted events and resolved profile (18.4), feeding
   * `activity.json` and the "what your agents did" report section; computed by the caller, once,
   * since it is also needed for the terminal summary and the `--json` envelope. A caller that leaves
   * it out gets the honest answer for a profile that declares nothing. */
  activity?: Activity;
  /** `computeBlindSpots` over `assertions` and the same `profile`/`catalogObjects`/`events` the
   * caller already has in scope (18.5), feeding `blind-spots.json` and the not-enough-evidence
   * report section; computed by the caller, once, since it is also needed for the terminal summary
   * and the `--json` envelope. A caller that leaves it out gets the honest empty answer. */
  blindSpots?: BlindSpots;
  /** The run's accepted events (18.14 C3): needed only to re-summarise activity per subject when
   * `profile` names more than one subject; a single subject (or no `profile`) never reads this. */
  events?: Event[];
  /** 18.14 (Hill 7): when `profile` names more than one subject, every agent's records go side by
   * side -- `project.md`/`.html`/`.json` (`computeProjectView`) plus each subject's own full report
   * under `agents/<dirname>/`, and the root `report.md`/`.html` become the project view. A single
   * subject (or no `profile`) writes exactly what this function always wrote. */
  profile?: Profile;
  /** Mirrors `summarizeActivity`'s own parameter and default: every subject in `profile` counts as
   * declared when omitted. */
  declaredSubjectIds?: ReadonlySet<string>;
  /** What `--allow-unverified-catalog` waived (SPEC §8.7, 18.36); forwarded to `buildManifest`. */
  limitations?: string[];
}

function bareSubject(id: string): Subject {
  return {
    id,
    name: null,
    role: null,
    evidenceSources: [],
    coverageDenominators: [],
    declaredDecisionTypes: [],
    declaredOversight: {},
    declaredComponents: [],
    declaredTools: [],
    declaredModels: [],
  };
}

/** Write every report artifact for `assertions` and return the reproducibility manifest. */
export function writeReport(
  outDir: string,
  assertions: Assertion[],
  options: WriteReportOptions,
): Record<string, unknown> {
  checkDc5(assertions); // DC-5: refuse a supporting verdict without an evidence pointer
  mkdirSync(outDir, { recursive: true });
  const outputs: Record<string, string> = {};

  const writeJson = (name: string, obj: unknown): void => {
    const data = canonicalize(obj);
    writeFileSync(join(outDir, name), data);
    outputs[name] = digestBytes(data);
  };
  const writeTextFile = (name: string, text: string): void => {
    const data = Buffer.from(text, "utf-8");
    writeFileSync(join(outDir, name), data);
    outputs[name] = digestBytes(data);
  };

  const language = options.reportLanguage ?? DEFAULT_LANGUAGE;
  const counts = aggregate(assertions);
  const activity = options.activity ?? summarizeActivity([], profileFromDict({}));
  const blindSpots = options.blindSpots ?? { blind_spots: [], no_population: [] };
  writeJson("assertions.json", assertions.map(assertionToJson));
  writeJson("activity.json", activity);
  writeJson("blind-spots.json", blindSpots);

  // 18.14 C3: every agent's records side by side (Hill 7) -- only when `profile` names more than
  // one subject; a single subject (or no `profile`) leaves every byte below unchanged.
  let projectView: ProjectView | undefined;
  const projectActivityBySubject = new Map<string, Activity>();
  const profile = options.profile;
  if (profile !== undefined && profile.subjects.length > 1) {
    const resolvedDeclared = new Set(
      options.declaredSubjectIds ?? profile.subjects.map((s) => s.id),
    );
    const projectSubjectIds = [
      ...new Set([...assertions.map((a) => a.subject), ...profile.subjects.map((s) => s.id)]),
    ].sort(byteCompare);
    const eventsBySubject = indexBySubject(options.events ?? []);
    const declaredSubjectsById = new Map(profile.subjects.map((s) => [s.id, s]));
    for (const subjectId of projectSubjectIds) {
      const declaredSubject = declaredSubjectsById.get(subjectId);
      const subjectProfile: Profile = {
        ...profile,
        subjects: [declaredSubject ?? bareSubject(subjectId)],
      };
      const subjectDeclaredIds = resolvedDeclared.has(subjectId)
        ? new Set([subjectId])
        : new Set<string>();
      projectActivityBySubject.set(
        subjectId,
        summarizeActivity(eventsBySubject.get(subjectId) ?? [], subjectProfile, subjectDeclaredIds),
      );
    }
    projectView = computeProjectView(
      assertions,
      profile,
      resolvedDeclared,
      projectActivityBySubject,
      blindSpots,
    );
    writeJson("project.json", projectView);
    const gapsBySubject = blindSpotsBySubject(blindSpots);
    const noPopBySubject = noPopulationBySubject(blindSpots.no_population);
    const assertionsBySubject = new Map<string, Assertion[]>();
    for (const assertion of assertions) {
      const list = assertionsBySubject.get(assertion.subject);
      if (list) {
        list.push(assertion);
      } else {
        assertionsBySubject.set(assertion.subject, [assertion]);
      }
    }
    for (const subjectId of projectSubjectIds) {
      const subjectDirname = agentDirname(subjectId);
      mkdirSync(join(outDir, "agents", subjectDirname), { recursive: true });
      const subjectAssertions = assertionsBySubject.get(subjectId) ?? [];
      // Populated above for every id in `projectSubjectIds`, including this one.
      const subjectActivity = projectActivityBySubject.get(subjectId) as Activity;
      const subjectBlindSpots: BlindSpots = {
        blind_spots: gapsBySubject.get(subjectId) ?? [],
        no_population: noPopBySubject.get(subjectId) ?? [],
      };
      writeJson(`agents/${subjectDirname}/assertions.json`, subjectAssertions.map(assertionToJson));
      writeJson(`agents/${subjectDirname}/activity.json`, subjectActivity);
      writeJson(`agents/${subjectDirname}/blind-spots.json`, subjectBlindSpots);
      writeTextFile(
        `agents/${subjectDirname}/report.md`,
        renderReportMd(
          subjectAssertions,
          aggregate(subjectAssertions),
          language,
          subjectActivity,
          subjectBlindSpots,
        ),
      );
      writeTextFile(
        `agents/${subjectDirname}/report.html`,
        renderReportHtml(
          subjectAssertions,
          aggregate(subjectAssertions),
          language,
          subjectActivity,
          subjectBlindSpots,
        ),
      );
    }
  }

  if (projectView !== undefined) {
    const projectMd = renderProjectMd(projectView, projectActivityBySubject, language);
    writeTextFile("project.md", projectMd);
    writeTextFile("report.md", projectMd);
    const projectHtml = renderProjectHtml(projectView, projectActivityBySubject, language);
    writeTextFile("project.html", projectHtml);
    writeTextFile("report.html", projectHtml);
  } else {
    writeTextFile("report.md", renderReportMd(assertions, counts, language, activity, blindSpots));
    writeTextFile(
      "report.html",
      renderReportHtml(assertions, counts, language, activity, blindSpots),
    );
  }
  writeJson("oscal-ar.json", renderOscal(assertions));
  writeJson("results.sarif", renderSarif(assertions, options.catalogObjects ?? []));

  const subjects = [...new Set(assertions.map((a) => a.subject))].sort(byteCompare);
  for (const subject of subjects) {
    const pack = renderEvidencePack(
      subject,
      assertions.filter((a) => a.subject === subject),
    );
    const rel = `packs/${safe(subject)}/pack.json`;
    const data = canonicalize(pack);
    const path = join(outDir, rel);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, data);
    outputs[rel] = digestBytes(data);
  }

  const manifest = buildManifest({
    bundleDigest: options.bundleDigest,
    catalogs: options.catalogs,
    catalogObjects: options.catalogObjects,
    outputs,
    operator: options.operator ?? "unknown",
    invocation: options.invocation ?? [],
    supersedes: options.supersedes ?? [],
    reportLanguage: language,
    limitations: options.limitations,
  });
  writeFileSync(join(outDir, "manifest.json"), JSON.stringify(sortKeysDeep(manifest), null, 2));
  return manifest;
}
