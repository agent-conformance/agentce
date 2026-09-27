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
import { mkdirSync, writeFileSync } from "node:fs";
import { arch, platform } from "node:os";
import { dirname, join } from "node:path";
import type { Activity } from "./activity";
import { DENIED_KINDS, RECORDER_CLASSES, summarizeActivity } from "./activity";
import { type Assertion, aggregate, assertionToJson, checkDc5 } from "./assertions";
import type { BlindSpot, BlindSpots, CheckRef } from "./blindSpots";
import { canonicalize } from "./canonical";
import type { Catalog, ControlSpec } from "./catalog";
import { DEFAULT_LANGUAGE, catalogue } from "./messages";
import { profileFromDict } from "./profile";
import { byteCompare, sortKeysDeep } from "./util";
import { ENGINE_NAME, SPEC_VERSION, engineVersion } from "./version";

const ZERO_DIGEST = `sha256:${"0".repeat(64)}`;
const NAMESPACE_URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";

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

/** Cap applied when an event-derived string is escaped for Markdown/terminal rendering (SPEC §7
 * injection hardening): long enough to stay useful, short enough to bound a hostile payload. */
const MD_ESCAPE_CAP = 200;

/** Shown in place of a name that neutralises to nothing: escaping, never erasure -- real activity
 * is never silently dropped to "0". */
const MD_ESCAPE_EMPTY_PLACEHOLDER = "(unnamed)";

/** C0/C1 controls (newline, tab, the ESC that starts a terminal escape sequence, NEL U+0085, ...)
 * plus the Unicode line/paragraph separators U+2028/U+2029 -- every codepoint that can move the
 * terminal cursor, start a new line, or hide text, not only whitespace. */
function isControlLike(codepoint: number): boolean {
  return (
    codepoint <= 0x1f ||
    codepoint === 0x7f ||
    (codepoint >= 0x80 && codepoint <= 0x9f) ||
    codepoint === 0x2028 ||
    codepoint === 0x2029
  );
}

/** Neutralise an event-derived string (agent, model, or tool name) before it reaches `report.md` or
 * the terminal (SPEC §7 injection hardening, mirroring the Python reference's `_md_escape`): replace
 * every control character and line/paragraph separator with a space (never just whitespace -- a raw
 * ESC can still write a hostile terminal escape sequence), collapse the result to single spaces,
 * replace backticks and angle brackets with visually similar but inert characters (round 3: a
 * `<br>`/`<h2>` in a hostile name would otherwise pass through as live HTML when the Markdown is
 * rendered by a browser and forge its own heading/line break -- never escaped as `&lt;`/`&gt;`,
 * which would defeat plain-text/terminal readability), and cap its length by codepoint (`Array.from`,
 * never a UTF-16 half of a surrogate pair). A string that neutralises to nothing renders as
 * `MD_ESCAPE_EMPTY_PLACEHOLDER`, never a silent gap. */
function mdEscape(text: string, cap: number = MD_ESCAPE_CAP): string {
  const neutralized = Array.from(text)
    .map((ch) => (isControlLike(ch.codePointAt(0) as number) ? " " : ch))
    .join("");
  let collapsed = neutralized
    .split(/\s+/)
    .filter((w) => w.length > 0)
    .join(" ")
    .replace(/`/g, "'")
    .replace(/</g, "‹")
    .replace(/>/g, "›");
  if (collapsed.length === 0 && text.length > 0) {
    collapsed = MD_ESCAPE_EMPTY_PLACEHOLDER;
  }
  if (collapsed.length <= cap) {
    // UTF-16 length is always >= codepoint count, so this is a safe, cheap sufficient condition
    // to skip the codepoint-array build below for the common (short, uncapped) name.
    return collapsed;
  }
  const codepoints = Array.from(collapsed);
  return codepoints.length > cap ? `${codepoints.slice(0, cap - 1).join("")}…` : collapsed;
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
  undeclared: { tools: string[]; models: string[] },
  cat: Record<string, string>,
): string[] {
  if (undeclared.tools.length === 0 && undeclared.models.length === 0) {
    return [cat["report.activity_none_undeclared"] as string];
  }
  const lines: string[] = [];
  if (undeclared.tools.length > 0) {
    lines.push(
      `${cat["report.activity_undeclared_tools_label"]}: ` +
        `${undeclared.tools.map((name) => mdEscape(name)).join(", ")}`,
    );
  }
  if (undeclared.models.length > 0) {
    lines.push(
      `${cat["report.activity_undeclared_models_label"]}: ` +
        `${undeclared.models.map((name) => mdEscape(name)).join(", ")}`,
    );
  }
  return lines;
}

/** `(label, value)` for every counted-facts row -- the one place the row set and order is decided,
 * shared by the Markdown, HTML, and terminal renderings. Agent, model, and tool names are
 * event-derived strings (SPEC §7 injection hardening), escaped with `mdEscape` before joining so a
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
        ? activity.agents.map((a) => mdEscape(a)).join(", ")
        : (cat["report.activity_none_agents"] as string),
    ],
    [
      cat["report.activity_models_label"] as string,
      activity.models.map((m) => mdEscape(m.name)).join(", ") || "0",
    ],
    [
      cat["report.activity_tools_label"] as string,
      activity.tools.map((t) => mdEscape(t.name)).join(", ") || "0",
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
  return stepKind === "request" ? `a request to ${ownerLabel}` : `a code change for ${ownerLabel}`;
}

/** `(label, value)` for every blind spot, in the module's own ranked order (never re-sorted here). */
function blindSpotRows(blindSpots: BlindSpot[]): [string, string][] {
  return blindSpots.map((bs) => {
    const ownerLabel = OWNER_LABEL[bs.owner_key] as string;
    const step = blindSpotStepText(bs.step_kind, ownerLabel);
    const adapters = bs.supplying_adapters.join(", ") || "no adapter today";
    const value =
      `unlocks ${bs.checks_unlocked} check(s), needed by ${bs.needed_by} more; ` +
      `rung ${bs.ladder_rung} -- ${step}. Adapters that can supply this: ${adapters}.`;
    return [`${bs.event} (${bs.class})`, value];
  });
}

function noPopulationRows(noPopulation: CheckRef[]): [string, string][] {
  return noPopulation.map((entry) => [
    `${entry.control} on ${entry.subject} (${entry.catalog}@${entry.control_version})`,
    `The records show every kind of evidence ${entry.control} asks for, but not enough of it in the shape the control expects -- a --domain binding may be needed to identify the relevant decisions; see the control's documentation for what it needs.`,
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
  for (const [label, value] of rows) lines.push(`- ${label}: ${value}`);
  if (noPopRows.length > 0) {
    lines.push("", "### Records that don't show enough, with no single fix", "");
    for (const [label, value] of noPopRows) lines.push(`- ${label}: ${value}`);
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
  lines.push(`## ${cat["report.summary_heading"]}`, "");
  for (const [outcome, count] of Object.entries(counts)) {
    lines.push(`- ${outcome}: ${count}`);
  }
  lines.push("", `## ${cat["report.assertions_heading"]}`, "");
  if (assertions.length === 0) {
    lines.push(`_${cat["report.no_controls"]}_`);
  }
  for (const a of [...assertions].sort(bySubjectControl)) {
    lines.push(
      `- \`${a.control}\` @ \`${a.subject}\` -> **${a.outcome}** ` +
        `(rung ${a.rung}, ${a.mode}; ${a.population[1]}/${a.population[0]} failed)`,
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
    .map(([o, c]) => `<li>${escapeHtml(o)}: ${c}</li>`)
    .join("");
  const rows = [...assertions]
    .sort(bySubjectControl)
    .map(
      (a) =>
        `<tr><td>${escapeHtml(a.control)}</td><td>${escapeHtml(a.subject)}</td>` +
        `<td>${escapeHtml(a.outcome)}</td></tr>`,
    )
    .join("");
  const bodyRows =
    rows || `<tr><td colspan="3">${escapeHtml(cat["report.no_controls"] as string)}</td></tr>`;
  const csp = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'";
  const activitySection = activity !== undefined ? activityHtml(activity, cat) : "";
  const blindSpotsSection = blindSpots !== undefined ? blindSpotsHtml(blindSpots) : "";
  return `<!doctype html><html lang="${escapeHtml(language)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${csp}"><title>${title}</title><style>${HTML_STYLE}</style></head><body><main><h1>${title}</h1>${activitySection}${blindSpotsSection}<section aria-labelledby="summary"><h2 id="summary">${escapeHtml(cat["report.summary_heading"] as string)}</h2><ul>${summary}</ul></section><section aria-labelledby="assertions"><h2 id="assertions">${escapeHtml(cat["report.assertions_heading"] as string)}</h2><table><tr><th>Control</th><th>Subject</th><th>Outcome</th></tr>${bodyRows}</table></section></main></body></html>\n`;
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
    const key = `${a.control} ${a.subject}`;
    const obsUuid = uuid5("observation", a.control, a.subject);
    observationUuid.set(key, obsUuid);
    const observation: Record<string, unknown> = {
      uuid: obsUuid,
      description: `Assessment activity for ${a.control} on ${a.subject}.`,
      methods: ["TEST"],
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
    const key = `${a.control} ${a.subject}`;
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
  outputs: Record<string, string>;
  operator: string;
  invocation: string[];
  supersedes: string[];
  reportLanguage?: string;
}

export function buildManifest(options: ManifestOptions): Record<string, unknown> {
  const pkgDigest = packageDigest();
  const host = createHash("sha256").update(`${platform()}|${arch()}|${pkgDigest}`).digest("hex");
  const catalogRefs = options.catalogs.map((entry) => {
    const at = entry.indexOf("@");
    const cid = at >= 0 ? entry.slice(0, at) : entry;
    const version = at >= 0 ? entry.slice(at + 1) : "";
    return { id: cid, version: version || "0", digest: ZERO_DIGEST };
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
  writeTextFile("report.md", renderReportMd(assertions, counts, language, activity, blindSpots));
  writeTextFile(
    "report.html",
    renderReportHtml(assertions, counts, language, activity, blindSpots),
  );
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
    outputs,
    operator: options.operator ?? "unknown",
    invocation: options.invocation ?? [],
    supersedes: options.supersedes ?? [],
    reportLanguage: language,
  });
  writeFileSync(join(outDir, "manifest.json"), JSON.stringify(sortKeysDeep(manifest), null, 2));
  return manifest;
}
