/**
 * `agentce readiness` (SPEC §13.3.4): the report-readiness verdict (READY / READY WITH LIMITATIONS /
 * NOT READY) and the deviation-register linter it applies when a register is given. A byte-for-byte
 * port of the Python reference's `readiness.py` (`compute_readiness`, `deviation_lint`,
 * `normalize_deviation_dates`, `parse_date`), mirroring `diff.ts`'s shape as the compute seam
 * `cli.ts`'s `cmdReadiness` wires up.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { DEFAULT_SCHEMA, FAILSAFE_SCHEMA, Type as YamlType, load as yamlLoad } from "js-yaml";
import { InputError } from "./errors";
import { byteCompare, decodeUtf8Strict, normalizePosixPath, pyRepr, pyStr } from "./util";

export const READY = "READY";
export const READY_WITH_LIMITATIONS = "READY WITH LIMITATIONS";
export const NOT_READY = "NOT READY";

/** Default maximum deviation lifetime in days (SPEC §13.3.4; a catalog may set its own). */
export const DEFAULT_MAX_DEVIATION_DAYS = 180;

/** Integrity statuses that block a report from being signed (SPEC §13.3.4). */
const GAP_INTEGRITY_STATUSES = new Set(["failed", "gap", "reordered"]);
/** The integrity and coverage family whose findings can never be accepted as a deviation (§13.3.4). */
const INT_FAMILY = "INT";
const DEVIATION_FIELDS = [
  "rationale",
  "compensating_control",
  "owner",
  "approver",
  "granted",
  "expiry",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Python's implicit dict-falsiness: `None`/`False`/`0`/`""`/an empty list/an empty mapping. */
export function pyTruthy(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === 0 || value === "") {
    return false;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (isRecord(value)) {
    return Object.keys(value).length > 0;
  }
  return true;
}

/** Python's `dict.get(key, default)`: substitutes `default` only when `key` is *absent*, never when
 * it is present with a falsy or `null` value -- the distinction a naive `obj[key] ?? default` or
 * `obj[key] || default` collapses. */
function pyGet(obj: Record<string, unknown>, key: string, defaultValue: unknown): unknown {
  return key in obj ? obj[key] : defaultValue;
}

/** Python's `==` for a YAML-parsed value: recursive structural equality over strings, numbers,
 * booleans, `null`, arrays, and plain mappings -- `owner`/`approver` can hold any of these on hostile
 * input, not only a string. */
function pyEquals(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => pyEquals(v, b[i]));
  }
  if (isRecord(a) && isRecord(b)) {
    const keysA = Object.keys(a);
    const keysB = Object.keys(b);
    return keysA.length === keysB.length && keysA.every((k) => k in b && pyEquals(a[k], b[k]));
  }
  return false;
}

// --- Deviation-register YAML loading (PyYAML-matching implicit resolution) --------------------

/** A YAML scalar PyYAML's `SafeLoader` would resolve to a `date`/`datetime` -- the raw, unrewritten
 * matched text, never a JS `Date`, so a downstream reader can tell "this was unquoted, i.e. Python's
 * own reference rewrites its text via `str(datetime)`" apart from "this is a string that happens to
 * look like one" (a quoted scalar with the identical text), a distinction `CORE_SCHEMA` (every scalar
 * looks quoted) and js-yaml's built-in timestamp type (every plain timestamp becomes a `Date`, thrown
 * away by `str(datetime)`'s different rendering) both erase in opposite directions. */
export interface PyyamlTimestamp {
  readonly pyyamlTimestamp: string;
}

export function isPyyamlTimestamp(value: unknown): value is PyyamlTimestamp {
  return isRecord(value) && typeof value.pyyamlTimestamp === "string";
}

/** PyYAML's own `Resolver`, `tag:yaml.org,2002:bool` implicit-resolution regex (`yaml/resolver.py`),
 * copied verbatim (stripped of `re.X`'s insignificant whitespace). */
const PYYAML_BOOL_RESOLVE_RE =
  /^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$/;
/** PyYAML's `construct_yaml_bool`: lower-case, then a fixed three-word truthy set. */
const PYYAML_BOOL_TRUE = new Set(["yes", "true", "on"]);

/** PyYAML's own `Resolver`, `tag:yaml.org,2002:timestamp` implicit-resolution regex
 * (`yaml/resolver.py`), copied verbatim (stripped of `re.X`'s insignificant whitespace) -- this is
 * the "is this plain scalar a timestamp at all" gate, deliberately narrower than the constructor's
 * own field-extraction regex below (a bare, single-digit-month/day date with no time component, e.g.
 * `2021-1-5`, never matches this gate and so is never even offered to the constructor -- confirmed
 * against real PyYAML: it stays a plain string). */
const PYYAML_TIMESTAMP_RESOLVE_RE =
  /^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)$/;

/** PyYAML's own `Constructor.timestamp_regexp` (`yaml/constructor.py:310-320`), copied verbatim
 * (stripped of `re.X`'s insignificant whitespace, named groups renamed only to be valid JS regex
 * group identifiers where PyYAML uses `tz_sign`/`tz_hour`/`tz_minute`, kept as-is since underscores
 * are valid there too) -- used only to pull fields out of text a marker already carries; never used
 * as the "is this a timestamp" gate (that is {@link PYYAML_TIMESTAMP_RESOLVE_RE} above). */
const PYYAML_TIMESTAMP_FIELDS_RE =
  /^(?<year>[0-9][0-9][0-9][0-9])-(?<month>[0-9][0-9]?)-(?<day>[0-9][0-9]?)(?:(?:[Tt]|[ \t]+)(?<hour>[0-9][0-9]?):(?<minute>[0-9][0-9]):(?<second>[0-9][0-9])(?:\.(?<fraction>[0-9]*))?(?:[ \t]*(?<tz>Z|(?<tz_sign>[-+])(?<tz_hour>[0-9][0-9]?)(?::(?<tz_minute>[0-9][0-9]))?))?)?$/;

const pyyamlBoolType = new YamlType("tag:yaml.org,2002:bool", {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_BOOL_RESOLVE_RE.test(data),
  construct: (data: string) => PYYAML_BOOL_TRUE.has(data.toLowerCase()),
});

const pyyamlTimestampType = new YamlType("tag:yaml.org,2002:timestamp", {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_TIMESTAMP_RESOLVE_RE.test(data),
  construct: (data: string): PyyamlTimestamp => ({ pyyamlTimestamp: data }),
});

/** PyYAML's own `Resolver`, `tag:yaml.org,2002:int` implicit-resolution regex (`yaml/resolver.py`),
 * copied verbatim (stripped of `re.X`'s insignificant whitespace) -- deliberately narrower than
 * js-yaml's own built-in int type (YAML 1.2, which also accepts a bare `0o` octal prefix PyYAML's
 * YAML-1.1-based grammar never does: confirmed against real PyYAML, `0o17` stays the plain string
 * `'0o17'`), and confirmed to accept what PyYAML's own grammar does: `0x`/`0b` prefixes, a
 * leading-zero octal run, and a colon-separated sexagesimal integer. */
const PYYAML_INT_RESOLVE_RE =
  /^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$/;

/** PyYAML's own `Resolver`, `tag:yaml.org,2002:float` implicit-resolution regex (`yaml/resolver.py`),
 * copied verbatim (stripped of `re.X`'s insignificant whitespace) -- js-yaml's own built-in float
 * type disagrees on several of these forms (confirmed against real PyYAML), so this is used instead
 * of js-yaml's implicit type, exactly like {@link PYYAML_INT_RESOLVE_RE} above. */
const PYYAML_FLOAT_RESOLVE_RE =
  /^(?:[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$/;

/** PyYAML's `construct_yaml_int` (`yaml/constructor.py:237-263`), copied verbatim: strip `_`, take
 * the sign, then dispatch on the `0b`/`0x`/leading-`0`/colon-sexagesimal/plain-decimal forms in that
 * exact order (order matters here, unlike the resolve regex above, since these prefixes overlap
 * textually -- `0b101` starts with `0` too). Returns a plain JS `number`: every value this codebase's
 * own adversarial fixtures exercise fits in `number`'s safe integer range, and downstream code only
 * ever calls {@link pyStr}/{@link pyTruthy}-equivalent checks or `pyEquals` on the result, never
 * arithmetic that would need arbitrary precision. */
/** PyYAML's colon-sexagesimal base-60 place-value sum (`int(v, 60)`-equivalent for a
 * `":"`-separated value, e.g. `"1:30"` -> `90`), shared between {@link pyyamlConstructInt} and
 * {@link pyyamlConstructFloat} since both dispatch to it identically. */
function sexagesimalToNumber(value: string): number {
  const digits = value.split(":").map(Number).reverse();
  let base = 1;
  let out = 0;
  for (const digit of digits) {
    out += digit * base;
    base *= 60;
  }
  return out;
}

function pyyamlConstructInt(data: string): number {
  let value = data.replace(/_/g, "");
  let sign = 1;
  if (value[0] === "-") {
    sign = -1;
  }
  if (value[0] === "+" || value[0] === "-") {
    value = value.slice(1);
  }
  if (value === "0") {
    return 0;
  }
  if (value.startsWith("0b")) {
    return sign * Number.parseInt(value.slice(2), 2);
  }
  if (value.startsWith("0x")) {
    return sign * Number.parseInt(value.slice(2), 16);
  }
  if (value[0] === "0") {
    return sign * Number.parseInt(value, 8);
  }
  if (value.includes(":")) {
    return sign * sexagesimalToNumber(value);
  }
  return sign * Number.parseInt(value, 10);
}

/** PyYAML's `construct_yaml_float` (`yaml/constructor.py:270-292`), copied verbatim: lower-case,
 * strip `_`, take the sign, then dispatch on `.inf`/`.nan`/colon-sexagesimal/plain-decimal. */
function pyyamlConstructFloat(data: string): number {
  let value = data.replace(/_/g, "").toLowerCase();
  let sign = 1;
  if (value[0] === "-") {
    sign = -1;
  }
  if (value[0] === "+" || value[0] === "-") {
    value = value.slice(1);
  }
  if (value === ".inf") {
    return sign * Number.POSITIVE_INFINITY;
  }
  if (value === ".nan") {
    return Number.NaN;
  }
  if (value.includes(":")) {
    return sign * sexagesimalToNumber(value);
  }
  return sign * Number(value);
}

const pyyamlIntType = new YamlType("tag:yaml.org,2002:int", {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_INT_RESOLVE_RE.test(data),
  construct: pyyamlConstructInt,
});

const pyyamlFloatType = new YamlType("tag:yaml.org,2002:float", {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_FLOAT_RESOLVE_RE.test(data),
  construct: pyyamlConstructFloat,
});

/** `yaml.DEFAULT_SCHEMA`'s own compiled implicit type for `null`, unchanged -- js-yaml's package
 * `exports` field blocks a deep import of its internal `lib/type/*` modules, so this is the only
 * public way to reuse its already-PyYAML-matching `null` implicit type verbatim (confirmed against
 * real PyYAML separately from `int`/`float` above, which needed their own ported types instead). */
interface CompiledType extends YamlType {
  readonly tag: string;
}

function defaultImplicit(tag: string): YamlType {
  const compiled = (DEFAULT_SCHEMA as unknown as { compiledImplicit: CompiledType[] })
    .compiledImplicit;
  const found = compiled.find((t) => t.tag === tag);
  if (found === undefined) {
    throw new Error(`js-yaml DEFAULT_SCHEMA is missing the implicit type ${tag}`);
  }
  return found;
}

/** The schema {@link loadDeviationRegister} loads with: `FAILSAFE_SCHEMA` (only strings, arrays, and
 * plain mappings -- no `Date`-constructing types at all) extended with PyYAML-matching implicit
 * resolution for `null`/`bool`/`int`/`float`/`timestamp`, so a *quoted* scalar is always a plain
 * string (exactly like PyYAML) while an *unquoted* one resolves the same type PyYAML's own
 * `SafeLoader` would give it -- never `CORE_SCHEMA` (which erases the quoted/unquoted distinction
 * entirely, the opposite-direction bug from js-yaml's built-in `Date`-constructing timestamp type).
 * Used by `loadDeviationRegister` below and by `profile.ts`'s `loadProfile` (a profile `agentce
 * assess <folder>` derived is written by PyYAML and must read back as PyYAML reads it);
 * `catalog.ts`'s YAML `load` calls elsewhere in this engine are untouched. */
export const PYYAML_SAFE_SCHEMA = FAILSAFE_SCHEMA.extend({
  implicit: [
    defaultImplicit("tag:yaml.org,2002:null"),
    pyyamlBoolType,
    pyyamlIntType,
    pyyamlFloatType,
    pyyamlTimestampType,
    // `<<: *anchor` merge keys (PyYAML's `SafeLoader` resolves and flattens these by default via
    // `Resolver`/`SafeConstructor.flatten_mapping`, confirmed this item) -- reused from
    // `DEFAULT_SCHEMA` like the `null` type above, since js-yaml's own merge-key handling in
    // `storeMappingPair` triggers purely on this tag, keyed on the exact same `<<` scalar PyYAML
    // matches; an explicit key still overrides a merged-in one, since js-yaml applies the merged
    // pairs before the node's own explicit pairs, matching `flatten_mapping`'s ordering.
    defaultImplicit("tag:yaml.org,2002:merge"),
  ],
});

/** PyYAML's `construct_yaml_timestamp` (`yaml/constructor.py:322-351`) plus Python's own
 * `str(date)`/`str(datetime)` rendering, given a {@link PyyamlTimestamp} marker's raw text -- never a
 * plain string, which {@link normalizeDeviationDates} passes through completely unchanged. Performs
 * **no range validation**: an out-of-range field renders anyway (a syntactically-plausible but
 * semantically invalid string); {@link parseDate} is the one place both a quoted and a
 * pythonized-unquoted value are range-checked, so both fail exactly the same way. */
export function pythonizeTimestamp(raw: string): string {
  const m = PYYAML_TIMESTAMP_FIELDS_RE.exec(raw);
  if (m === null || m.groups === undefined) {
    return raw;
  }
  const g = m.groups;
  const year = g.year?.padStart(4, "0") ?? "";
  const month = String(Number(g.month)).padStart(2, "0");
  const day = String(Number(g.day)).padStart(2, "0");
  if (g.hour === undefined) {
    return `${year}-${month}-${day}`;
  }
  const hour = String(Number(g.hour)).padStart(2, "0");
  const minute = g.minute ?? "";
  const second = g.second ?? "";
  let microsecond = 0;
  if (g.fraction !== undefined) {
    let fraction = g.fraction.slice(0, 6);
    while (fraction.length < 6) {
      fraction += "0";
    }
    microsecond = Number(fraction);
  }
  let out = `${year}-${month}-${day} ${hour}:${minute}:${second}`;
  if (microsecond !== 0) {
    out += `.${String(microsecond).padStart(6, "0")}`;
  }
  if (g.tz === undefined) {
    // naive: no offset suffix.
  } else if (g.tz_sign === undefined) {
    // a bare Z/z.
    out += "+00:00";
  } else {
    const tzHour = String(Number(g.tz_hour)).padStart(2, "0");
    const tzMinute = String(Number(g.tz_minute ?? "0")).padStart(2, "0");
    out += `${g.tz_sign}${tzHour}:${tzMinute}`;
  }
  return out;
}

/** Rewrites every {@link PyyamlTimestamp} marker value in every entry to its `pythonizeTimestamp`
 * text, in place of the marker; every other value (a plain string -- including an originally-quoted
 * timestamp-looking one, left byte-identical -- a number, a boolean, absent) passes straight through.
 * Ported once, shared conceptually with Java's identical rewrite step (Jackson's own "preserve the
 * source text verbatim" behaviour for a plain timestamp scalar is exactly as far from
 * `str(datetime)` as this marker design closes here) -- this changes no Python code; Python's own
 * `str(date|datetime)` rendering is the reference both ports match. */
export function normalizeDeviationDates(
  deviations: Record<string, unknown>[],
): Record<string, unknown>[] {
  return deviations.map((entry) => {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(entry)) {
      out[key] = isPyyamlTimestamp(value) ? pythonizeTimestamp(value.pyyamlTimestamp) : value;
    }
    return out;
  });
}

/** Loads and validates a deviation register's *shape* (SPEC §13.3.4): a mapping with a top-level
 * `deviations` list of mappings, refused as `input.deviation_invalid` rather than crashing on
 * hostile or malformed YAML -- ports `_load_deviation_register`'s three shape corrections exactly: a
 * Python-falsy top-level value ({@link pyTruthy}'s falsy set, not just `null`/`undefined`) is treated
 * as "no deviations"; a `deviations` key present with an explicit `null` value is a shape error
 * (via {@link pyGet}'s absence-only default, never `?? []`), distinct from the key being absent
 * (which silently defaults to `[]`); every `granted`/`expiry` value YAML resolved as a timestamp is
 * normalized via {@link normalizeDeviationDates} before use, mirroring Python's call-before-use
 * discipline. */
export function loadDeviationRegister(path: string): Record<string, unknown>[] {
  // Python passes the loader a `Path`, so its errors name `./r.yaml` as `r.yaml`.
  const where = `the deviation register at ${pyRepr(normalizePosixPath(path))}`;
  let text: string;
  try {
    // A fatal decode, as Python's `read_text(encoding="utf-8")`: a lossy one would turn a 0xff byte
    // into U+FFFD and lint the mangled control id instead of refusing the file.
    text = decodeUtf8Strict(readFileSync(path));
  } catch (error) {
    throw new InputError(
      "input.deviation_invalid",
      `${where} is not valid UTF-8: ${(error as Error).message}.`,
      "save the deviation register as UTF-8 text.",
    );
  }
  let parsed: unknown;
  try {
    // `json: true` disables js-yaml's own duplicate-mapping-key error (PyYAML's `SafeLoader` never
    // rejects a duplicate key -- `BaseConstructor.construct_mapping` just does `dict[key] = value` in
    // document order, so the last occurrence wins, confirmed this item).
    parsed = yamlLoad(text, { schema: PYYAML_SAFE_SCHEMA, json: true });
  } catch (error) {
    if (error instanceof RangeError || /exceeded maxDepth/.test((error as Error).message)) {
      // js-yaml caps nesting (and recurses per level), as PyYAML's composer hits its RecursionError.
      throw new InputError(
        "input.deviation_invalid",
        `${where} is nested too deeply to parse safely.`,
        "flatten the deviation register's structure; it exceeds the engine's safe nesting depth.",
      );
    }
    throw new InputError(
      "input.deviation_invalid",
      `${where} carries a YAML construct the engine refuses to load: ${(error as Error).message}.`,
      "remove custom tags and aliases from the deviation register; only plain YAML scalars, " +
        "mappings, and sequences are accepted.",
    );
  }
  const data: unknown = pyTruthy(parsed) ? parsed : {};
  if (!isRecord(data)) {
    throw new InputError(
      "input.deviation_invalid",
      `${where} is not a mapping.`,
      "the register must be a mapping with a top-level `deviations:` list.",
    );
  }
  const raw = pyGet(data, "deviations", []);
  if (!Array.isArray(raw)) {
    throw new InputError(
      "input.deviation_invalid",
      `${where}'s \`deviations\` key is not a list.`,
      "`deviations:` must be a list of deviation entries.",
    );
  }
  const entries: Record<string, unknown>[] = [];
  for (const entry of raw) {
    if (!isRecord(entry)) {
      throw new InputError(
        "input.deviation_invalid",
        `${where} has a deviation entry that is not a mapping.`,
        "each entry under `deviations:` must be a mapping of the register's own fields.",
      );
    }
    entries.push(entry);
  }
  return normalizeDeviationDates(entries);
}

/** The gaps-file token pattern (SPEC §13.3.4): a control id shaped `[A-Z]{2,4}-[0-9]{2}`, matched
 * with Unicode-aware word-boundary lookarounds reproducing Python's default (`re.UNICODE`) `\b` --
 * `[\p{L}\p{N}_]`, exactly Python's `\w` -- never JS's ASCII-only default `\b`, which over-matches a
 * control id embedded next to non-ASCII text (confirmed against an adversarial fixture: Python's
 * `re.findall` gives 1 match, the ASCII-only form gives 3). */
const GAPS_TOKEN_RE = /(?<![\p{L}\p{N}_])[A-Z]{2,4}-[0-9]{2}(?![\p{L}\p{N}_])/gu;

/** The set of control ids named in a gaps file, deduplicated -- mirrors
 * `set(re.findall(r"\b[A-Z]{2,4}-[0-9]{2}\b", text))`. */
export function parseGapsFile(text: string): Set<string> {
  return new Set(text.match(GAPS_TOKEN_RE) ?? []);
}

// --- parseDate: a pure calendar/clock-field record, never a JS Date --------------------------

/** A parsed ISO-8601 date or date-time as pure calendar/clock fields, never a JS `Date`/instant:
 * every comparison this module makes (`deviationLint`'s `> maxDays`, `< asOfDate`) is calendar-field
 * arithmetic in the value's own stated offset, never an instant on a shared timeline, because
 * UTC-normalizing through a `Date` is exactly the rollover hazard
 * (`2021-12-31T23:00:00-05:00` reading as `2022-01-01` in UTC) a field record has no timezone to
 * convert through and so cannot reproduce structurally. */
export interface CalendarDate {
  readonly year: number;
  readonly month: number;
  readonly day: number;
  readonly hour?: number;
  readonly minute?: number;
  readonly second?: number;
  readonly microsecond?: number;
  readonly offsetMinutes?: number | null;
}

/** RFC 3339 only, the one grammar all three engines accept (2026-09-30 maintainer decision,
 * `TRADEOFFS.md`/inbox row 19): only the forms the deviation-register schema's own
 * `format: date-time` allows -- never `datetime.fromisoformat`'s additionally-accepted basic format
 * (`20211231`), week-dates (`2021-W52-5`), or hour-only/minute-only reduced-precision forms; Python's
 * own `parse_date` now rejects these same forms too, so this is no longer a cross-engine divergence. */
const PARSE_DATE_RE =
  /^(?<year>\d{4})-(?<month>\d{2})-(?<day>\d{2})(?:[T ](?<hour>\d{2}):(?<minute>\d{2}):(?<second>\d{2})(?:\.(?<fraction>\d+))?(?<offset>Z|[+-]\d{2}:?\d{2})?)?$/;
const OFFSET_RE = /^([+-])(\d{2}):?(\d{2})$/;

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year: number, month: number): number {
  return month === 2 && isLeapYear(year) ? 29 : (DAYS_IN_MONTH[month - 1] as number);
}

/** Parses `value` against the grammar above and, on a match, **validates every field for real, not
 * just the calendar date** -- matching what a real `datetime.fromisoformat` rejects: year >= 1, the
 * days-in-month/leap-year rule, hour <= 23, minute <= 59, second <= 59, offset hour <= 23, offset
 * minute <= 59 when an offset is present. Returns `null` on any grammar or range failure -- never
 * throws, mirroring `parse_date`'s contract exactly. Only ever called on a plain string: a
 * `granted`/`expiry` value that was ever a YAML timestamp marker has already been rewritten to a
 * plain string by {@link normalizeDeviationDates} before either port's `deviation_lint`-equivalent
 * ever sees it, exactly as `_load_deviation_register`'s own call-before-use discipline guarantees for
 * Python. */
export function parseDate(value: string): CalendarDate | null {
  const m = PARSE_DATE_RE.exec(value);
  if (m === null || m.groups === undefined) {
    return null;
  }
  const g = m.groups;
  const year = Number(g.year);
  const month = Number(g.month);
  const day = Number(g.day);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) {
    return null;
  }
  if (g.hour === undefined) {
    return { year, month, day };
  }
  const hour = Number(g.hour);
  const minute = Number(g.minute);
  const second = Number(g.second);
  if (hour > 23 || minute > 59 || second > 59) {
    return null;
  }
  let microsecond: number | undefined;
  if (g.fraction !== undefined) {
    let fraction = g.fraction.slice(0, 6);
    while (fraction.length < 6) {
      fraction += "0";
    }
    microsecond = Number(fraction);
  }
  let offsetMinutes: number | null | undefined;
  if (g.offset === undefined) {
    offsetMinutes = undefined;
  } else if (g.offset === "Z") {
    offsetMinutes = 0;
  } else {
    const offsetMatch = OFFSET_RE.exec(g.offset);
    if (offsetMatch === null) {
      return null;
    }
    const offHour = Number(offsetMatch[2]);
    const offMinute = Number(offsetMatch[3]);
    if (offHour > 23 || offMinute > 59) {
      return null;
    }
    offsetMinutes = (offsetMatch[1] === "-" ? -1 : 1) * (offHour * 60 + offMinute);
  }
  return { year, month, day, hour, minute, second, microsecond, offsetMinutes };
}

/** The proleptic-Gregorian Julian day number of `date`'s calendar fields alone (never its
 * time-of-day) -- the standard integer algorithm, used only so {@link daysBetween}/{@link
 * compareDate} get real calendar-day arithmetic (accounting for month lengths and leap years) with
 * no `Date`/instant anywhere in the computation. */
function julianDayNumber({ year, month, day }: CalendarDate): number {
  const a = Math.floor((14 - month) / 12);
  const y = year + 4800 - a;
  const m = month + 12 * a - 3;
  return (
    day +
    Math.floor((153 * m + 2) / 5) +
    365 * y +
    Math.floor(y / 4) -
    Math.floor(y / 100) +
    Math.floor(y / 400) -
    32045
  );
}

/** `(expiry.date() - granted.date()).days`, calendar-day arithmetic on the `date()`-truncated
 * fields, matching Python's own `timedelta.days` for two `date` objects exactly. */
function daysBetween(from: CalendarDate, to: CalendarDate): number {
  return julianDayNumber(to) - julianDayNumber(from);
}

/** Negative when `a`'s calendar date is before `b`'s, matching Python's `date.__lt__`. */
export function compareDate(a: CalendarDate, b: CalendarDate): number {
  return julianDayNumber(a) - julianDayNumber(b);
}

// --- deviation_lint -----------------------------------------------------------------------------

export interface DeviationLintParams {
  readonly controlIds: Set<string>;
  readonly outcomesByControl: Map<string, Set<string>>;
  readonly appliedControls: Set<string>;
  readonly asOf: string | null;
  readonly maxDays?: number;
}

/** Enforces the deviation rules of SPEC §13.3.4, ported from `deviation_lint`
 * (`readiness.py:200-276`) exactly: the control exists and its outcome was `non-conformant` (never
 * `insufficient_evidence`), no deviation touches the INT family, every field is present (via {@link
 * pyTruthy}, so an empty list/mapping value counts as missing, not only an absent or `null` one), the
 * approver is a person distinct from the owner (via {@link pyEquals}, only when both are truthy), and
 * the deviation expires within the catalog's maximum lifetime. `outcomesByControl` carries every
 * outcome recorded for a control across every subject in this run (never a single collapsed value),
 * so the accept/reject verdict never depends on assertion iteration order. A control already in
 * `appliedControls` skips the outcome re-check entirely, but is instead checked, when `asOf` is
 * given, against its own expiry. */
export function deviationLint(
  deviations: Record<string, unknown>[],
  {
    controlIds,
    outcomesByControl,
    appliedControls,
    asOf,
    maxDays = DEFAULT_MAX_DEVIATION_DAYS,
  }: DeviationLintParams,
): string[] {
  const problems: string[] = [];
  const seenControls = new Set<string>();
  const asOfDate = asOf !== null ? parseDate(asOf) : null;
  for (const deviation of deviations) {
    const control = pyStr(pyGet(deviation, "control", ""));
    if (seenControls.has(control)) {
      problems.push(`${control}: duplicate deviation entry for this control`);
    }
    seenControls.add(control);
    if (!controlIds.has(control)) {
      problems.push(`${control || "<none>"}: control is not in the catalog`);
    }
    if (control.split("-")[0] === INT_FAMILY) {
      problems.push(`${control}: the INT family cannot be deviated (integrity and coverage)`);
    }
    if (!appliedControls.has(control)) {
      const outcomes = outcomesByControl.get(control) ?? new Set<string>();
      if (outcomes.has("insufficient_evidence")) {
        problems.push(
          `${control}: insufficient_evidence is an evidence gap, not a risk acceptance`,
        );
      } else if (outcomes.size > 0 && !outcomes.has("non-conformant")) {
        const sorted = [...outcomes].sort(byteCompare).join(", ");
        problems.push(`${control}: only a non-conformant outcome may be deviated (got ${sorted})`);
      }
    }
    for (const field of DEVIATION_FIELDS) {
      if (!pyTruthy(deviation[field])) {
        problems.push(`${control}: deviation is missing ${field}`);
      }
    }
    const owner = deviation.owner;
    const approver = deviation.approver;
    if (pyTruthy(owner) && pyTruthy(approver) && pyEquals(owner, approver)) {
      problems.push(`${control}: the approver must be a person distinct from the owner`);
    }
    const grantedRaw = deviation.granted;
    const expiryRaw = deviation.expiry;
    const granted = typeof grantedRaw === "string" ? parseDate(grantedRaw) : null;
    const expiry = typeof expiryRaw === "string" ? parseDate(expiryRaw) : null;
    for (const [dateField, raw, parsed] of [
      ["granted", grantedRaw, granted] as const,
      ["expiry", expiryRaw, expiry] as const,
    ]) {
      if (pyTruthy(raw) && parsed === null) {
        problems.push(`${control}: ${dateField} is not a valid RFC 3339 date (${pyRepr(raw)})`);
      }
    }
    if (granted !== null && expiry !== null && daysBetween(granted, expiry) > maxDays) {
      problems.push(`${control}: deviation lifetime exceeds ${maxDays} days`);
    }
    if (
      asOfDate !== null &&
      expiry !== null &&
      appliedControls.has(control) &&
      compareDate(expiry, asOfDate) < 0
    ) {
      problems.push(`${control}: applied deviation has expired (expiry ${pyStr(expiryRaw)})`);
    }
  }
  return problems;
}

// --- compute_readiness ----------------------------------------------------------------------

function readJsonl(path: string): Record<string, unknown>[] {
  if (!existsSync(path) || !statSync(path).isFile()) {
    return [];
  }
  const out: Record<string, unknown>[] = [];
  for (const rawLine of readFileSync(path, "utf-8").split("\n")) {
    const line = rawLine.trim();
    if (line) {
      out.push(JSON.parse(line));
    }
  }
  return out;
}

function readJsonFileOr<T>(path: string, fallback: T): T {
  if (!existsSync(path) || !statSync(path).isFile()) {
    return fallback;
  }
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

export interface ComputeReadinessOptions {
  readonly severities: Map<string, string>;
  readonly deviations?: Record<string, unknown>[];
  readonly gaps?: Set<string>;
}

export interface ReadinessVerdict {
  readonly verdict: string;
  readonly reasons: string[];
  readonly limitations: string[];
}

/** Returns the readiness verdict for a finished report directory (SPEC §13.3.4 stage 4), ported from
 * `compute_readiness` (`readiness.py:104-197`) exactly. `severities` maps control id to severity
 * (from the catalogs the report used); `gaps` is the set of control ids recorded in the operator's
 * gaps file with an owner and date. A high-severity `insufficient_evidence` outcome is a limitation
 * when recorded in `gaps` and a blocker otherwise; integrity breaks, coverage shortfalls,
 * applicability drift, and invalid deviations always block. `reasons`/`limitations` are
 * **multisets, not sets**: two subjects each producing the identical high-severity-gap reason string
 * yield two entries, not one, built with `push`, never a `Set`/`Map` keyed on the message text, and
 * sorted with {@link byteCompare}, never `Array.sort()`'s default UTF-16-code-unit order -- a
 * control id, subject, stream name, or drift `ref` can carry arbitrary report content. */
export function computeReadiness(
  reportDir: string,
  { severities, deviations, gaps }: ComputeReadinessOptions,
): ReadinessVerdict {
  const gapSet = gaps ?? new Set<string>();
  const reasons: string[] = [];
  const limitations: string[] = [];

  for (const record of readJsonl(join(reportDir, "integrity.jsonl"))) {
    const status = record.status;
    if (typeof status === "string" && GAP_INTEGRITY_STATUSES.has(status)) {
      reasons.push(`integrity ${pyStr(status)} on stream ${pyStr(record.stream)}`);
    }
  }

  const coverage = readJsonFileOr<Record<string, unknown>>(join(reportDir, "coverage.json"), {});
  const subjects = isRecord(coverage.subjects) ? coverage.subjects : {};
  for (const subject of Object.keys(subjects).sort(byteCompare)) {
    const entry = subjects[subject];
    if (!isRecord(entry)) {
      continue;
    }
    if (entry.coverage_status === "gap") {
      reasons.push(`coverage shortfall for subject ${subject}`);
    }
    const eventTypes = isRecord(entry.event_types) ? entry.event_types : {};
    for (const eventType of Object.keys(eventTypes).sort(byteCompare)) {
      const detail = eventTypes[eventType];
      // A declared source below the coverage threshold is a shortfall even when another event
      // type has unknown coverage and masks it in the subject-level roll-up (SPEC §13.3.4).
      if (isRecord(detail) && detail.status === "below_threshold") {
        reasons.push(`coverage shortfall for ${subject} ${eventType}`);
      }
    }
  }

  for (const statement of readJsonl(join(reportDir, "applicability.jsonl"))) {
    const driftRaw = pyGet(statement, "drift", []);
    const drift = pyTruthy(driftRaw) && Array.isArray(driftRaw) ? driftRaw : [];
    for (const entry of drift) {
      const d = isRecord(entry) ? entry : {};
      reasons.push(`applicability drift: ${pyStr(d.kind)} ${pyStr(d.ref)}`);
    }
  }

  const assertions = readJsonFileOr<Record<string, unknown>[]>(
    join(reportDir, "assertions.json"),
    [],
  );
  for (const assertion of assertions) {
    if (assertion.outcome !== "insufficient_evidence") {
      continue;
    }
    const control = pyStr(pyGet(assertion, "control", ""));
    if (severities.get(control) === "high") {
      if (gapSet.has(control)) {
        limitations.push(`${control}: high-severity evidence gap recorded`);
      } else {
        reasons.push(`${control}: unrecorded high-severity insufficient_evidence`);
      }
    }
  }

  if (deviations !== undefined && deviations.length > 0) {
    const outcomesByControl = new Map<string, Set<string>>();
    const appliedControls = new Set<string>();
    const windowEnds: string[] = [];
    for (const a of assertions) {
      const control = pyStr(a.control);
      let outcomes = outcomesByControl.get(control);
      if (outcomes === undefined) {
        outcomes = new Set<string>();
        outcomesByControl.set(control, outcomes);
      }
      outcomes.add(pyStr(a.outcome));
      if (pyTruthy(a.deviation)) {
        appliedControls.add(control);
      }
      const windowRaw = a.window;
      const window = pyTruthy(windowRaw) && isRecord(windowRaw) ? windowRaw : {};
      const end = window.end;
      if (pyTruthy(end)) {
        windowEnds.push(pyStr(end));
      }
    }
    const asOf =
      windowEnds.length > 0 ? (windowEnds.slice().sort(byteCompare).at(-1) as string) : null;
    const problems = deviationLint(deviations, {
      controlIds: new Set(severities.keys()),
      outcomesByControl,
      appliedControls,
      asOf,
    });
    for (const p of problems) {
      reasons.push(`invalid deviation: ${p}`);
    }
  }

  const verdict =
    reasons.length > 0 ? NOT_READY : limitations.length > 0 ? READY_WITH_LIMITATIONS : READY;
  return {
    verdict,
    reasons: reasons.slice().sort(byteCompare),
    limitations: limitations.slice().sort(byteCompare),
  };
}
