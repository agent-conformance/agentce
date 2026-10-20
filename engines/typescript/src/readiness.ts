/**
 * `agentce readiness` (SPEC §13.3.4): the report-readiness verdict (READY / READY WITH LIMITATIONS /
 * NOT READY) and the deviation-register linter it applies when a register is given. A byte-for-byte
 * port of the Python reference's `readiness.py` (`compute_readiness`, `deviation_lint`,
 * `normalize_deviation_dates`, `parse_date`), mirroring `diff.ts`'s shape as the compute seam
 * `cli.ts`'s `cmdReadiness` wires up.
 */

import { lstatSync, readFileSync, statSync } from "node:fs";
import {
  DEFAULT_SCHEMA,
  FAILSAFE_SCHEMA,
  YAMLException,
  Type as YamlType,
  load as yamlLoad,
} from "js-yaml";
import { InputError } from "./errors";
import { byteCompare, decodeUtf8Strict, joinPath, normalizePosixPath, pyRepr, pyStr } from "./util";

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
export function pyGet(obj: Record<string, unknown>, key: string, defaultValue: unknown): unknown {
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

const NULL_TAG = "tag:yaml.org,2002:null";
const BOOL_TAG = "tag:yaml.org,2002:bool";
const INT_TAG = "tag:yaml.org,2002:int";
const FLOAT_TAG = "tag:yaml.org,2002:float";
const TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp";
const MERGE_TAG = "tag:yaml.org,2002:merge";
const VALUE_TAG = "tag:yaml.org,2002:value";

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

/** `construct_yaml_bool`'s whole table: any other word under `!!bool` is refused. */
const PYYAML_BOOL_WORDS = new Set([...PYYAML_BOOL_TRUE, "no", "false", "off"]);

/** PyYAML's `construct_yaml_bool`: a word of its table, any case, or refused (a `! |` block's `yes\n` too). */
function pyyamlConstructBool(data: string): boolean {
  const word = data.toLowerCase();
  return constructed(
    PYYAML_BOOL_WORDS.has(word) ? PYYAML_BOOL_TRUE.has(word) : undefined,
    data,
    "bool",
  );
}

const pyyamlBoolType = new YamlType(BOOL_TAG, {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_BOOL_RESOLVE_RE.test(data),
  construct: pyyamlConstructBool,
});

/** Whether `construct_yaml_timestamp` builds a `date`/`datetime` from `raw`: its fields match and lie
 * in the calendar, the clock and a UTC offset under 24 hours. Otherwise `load_untrusted_yaml`'s loader
 * keeps the text as a string ({@link pythonizeTimestamp}). */
function isPyyamlCalendarTimestamp(raw: string): boolean {
  const g = PYYAML_TIMESTAMP_FIELDS_RE.exec(raw)?.groups;
  if (g === undefined) {
    return false;
  }
  const year = Number(g.year);
  const month = Number(g.month);
  const day = Number(g.day);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) {
    return false;
  }
  if (g.hour === undefined) {
    return true;
  }
  if (Number(g.hour) > 23 || Number(g.minute) > 59 || Number(g.second) > 59) {
    return false;
  }
  return g.tz_sign === undefined || Number(g.tz_hour) * 60 + Number(g.tz_minute ?? 0) < 24 * 60;
}

/** A timestamp scalar as `load_untrusted_yaml` reads it: a date marker, or the text when no date can be built. */
function pyyamlConstructTimestamp(text: string): PyyamlTimestamp | string {
  // The timestamp regex ends in Python's `$`, which also matches before one trailing newline (a `|` block).
  const data = text.endsWith("\n") ? text.slice(0, -1) : text;
  if (!isPyyamlCalendarTimestamp(data)) {
    return pythonizeTimestamp(text);
  }
  // Written as a mapping key, js-yaml names the key by the marker's own string tag and `toString`,
  // so two dates stay two keys (Python keeps both `date` keys) and neither equals a string key, as
  // Java's marker record names it.
  return Object.defineProperties(
    { pyyamlTimestamp: data },
    {
      [Symbol.toStringTag]: { value: "PyyamlTimestamp" },
      toString: { value: () => `PyyamlTimestamp[text=${data}]` },
    },
  );
}

const pyyamlTimestampType = new YamlType(TIMESTAMP_TAG, {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_TIMESTAMP_RESOLVE_RE.test(data),
  construct: pyyamlConstructTimestamp,
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

/** The whitespace Python's `int()` and `float()` ignore around a number (`str.isspace`). */
const PY_SPACE =
  "[\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]*";

/** `text` with each Unicode decimal digit (`١`, `１`, `𝟏`) written as its ASCII digit, as Python's `int()` and
 * `float()` read it first; other characters, `²` among them, are kept. Unicode lays each script's digits out
 * as one run from 0 to 9, so a digit's value is its distance from the run's start, mod 10. */
function asciiDigits(text: string): string {
  return NON_ASCII_DIGIT_RE.test(text) ? text.replace(NON_ASCII_DIGITS_RE, asciiDigit) : text;
}

const NON_ASCII_DIGIT_RE = /(?![0-9])\p{Nd}/u;
const NON_ASCII_DIGITS_RE = /(?![0-9])\p{Nd}/gu;

function asciiDigit(digit: string): string {
  const point = digit.codePointAt(0) ?? 0;
  let start = point;
  while (NON_ASCII_DIGIT_RE.test(String.fromCodePoint(start - 1))) {
    start -= 1;
  }
  return String((point - start) % 10);
}

/** Python's `int(text, base)`, after {@link asciiDigits}: an optional sign, the base's own optional prefix,
 * then digits. `undefined` where Python raises `ValueError`. */
const PY_INT_RE: Record<2 | 8 | 10 | 16, RegExp> = {
  2: new RegExp(`^${PY_SPACE}([-+]?)(?:0[bB])?([01]+)${PY_SPACE}$`),
  8: new RegExp(`^${PY_SPACE}([-+]?)(?:0[oO])?([0-7]+)${PY_SPACE}$`),
  10: new RegExp(`^${PY_SPACE}([-+]?)([0-9]+)${PY_SPACE}$`),
  16: new RegExp(`^${PY_SPACE}([-+]?)(?:0[xX])?([0-9a-fA-F]+)${PY_SPACE}$`),
};

function pythonInt(text: string, base: 2 | 8 | 10 | 16): number | undefined {
  const m = PY_INT_RE[base].exec(asciiDigits(text));
  if (m === null) {
    return undefined;
  }
  const n = Number.parseInt(m[2] ?? "", base);
  return m[1] === "-" ? -n : n;
}

/** Python's `float(text)`, after {@link asciiDigits}: decimal with an optional exponent, `inf`, `infinity`
 * or `nan`, any case, after an optional sign. */
const PY_FLOAT_RE = new RegExp(
  `^${PY_SPACE}([-+]?)(?:((?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:e[-+]?[0-9]+)?)|(inf|infinity)|(nan))${PY_SPACE}$`,
  "i",
);

function pythonFloat(text: string): number | undefined {
  const m = PY_FLOAT_RE.exec(asciiDigits(text));
  if (m === null) {
    return undefined;
  }
  const n =
    m[2] !== undefined ? Number(m[2]) : m[3] !== undefined ? Number.POSITIVE_INFINITY : Number.NaN;
  return m[1] === "-" ? -n : n;
}

/** PyYAML's colon-sexagesimal base-60 place-value sum (`"1:30"` -> `90`), each part read by `part`. */
function sexagesimal(
  value: string,
  part: (text: string) => number | undefined,
): number | undefined {
  let base = 1;
  let out = 0;
  for (const text of value.split(":").reverse()) {
    const digit = part(text);
    if (digit === undefined) {
      return undefined;
    }
    out += digit * base;
    base *= 60;
  }
  return out;
}

/** `value` with PyYAML's leading sign taken off, and that sign; `undefined` for empty text, where
 * PyYAML's `value[0]` raises. */
function pyyamlSign(value: string): [number, string] | undefined {
  if (value === "") {
    return undefined;
  }
  const sign = value[0] === "-" ? -1 : 1;
  return [sign, value[0] === "+" || value[0] === "-" ? value.slice(1) : value];
}

/** PyYAML's `construct_yaml_int` (`yaml/constructor.py:237-263`), copied verbatim: strip `_`, take
 * the sign, then dispatch on the `0b`/`0x`/leading-`0`/colon-sexagesimal/plain-decimal forms in that
 * exact order (order matters here, unlike the resolve regex above, since these prefixes overlap
 * textually -- `0b101` starts with `0` too), each read as Python's `int()` reads it; `undefined`
 * where PyYAML raises. Returns a plain JS `number`: downstream code only ever calls
 * {@link pyStr}/{@link pyTruthy}-equivalent checks or `pyEquals` on the result, never arithmetic
 * that would need arbitrary precision. */
function pyyamlConstructInt(data: string): number | undefined {
  const signed = pyyamlSign(data.replace(/_/g, ""));
  if (signed === undefined) {
    return undefined;
  }
  const [sign, value] = signed;
  let n: number | undefined;
  if (value === "0") {
    n = 0;
  } else if (value.startsWith("0b")) {
    n = pythonInt(value.slice(2), 2);
  } else if (value.startsWith("0x")) {
    n = pythonInt(value.slice(2), 16);
  } else if (value[0] === "0") {
    n = pythonInt(value, 8);
  } else if (value.includes(":")) {
    n = sexagesimal(value, (part) => pythonInt(part, 10));
  } else {
    n = pythonInt(value, 10);
  }
  return n === undefined ? undefined : n === 0 ? 0 : sign * n;
}

/** PyYAML's `construct_yaml_float` (`yaml/constructor.py:270-292`), copied verbatim: lower-case,
 * strip `_`, take the sign, then dispatch on `.inf`/`.nan`/colon-sexagesimal/plain-decimal, each
 * read as Python's `float()` reads it; `undefined` where PyYAML raises. */
function pyyamlConstructFloat(data: string): number | undefined {
  const signed = pyyamlSign(data.replace(/_/g, "").toLowerCase());
  if (signed === undefined) {
    return undefined;
  }
  const [sign, value] = signed;
  if (value === ".inf") {
    return sign * Number.POSITIVE_INFINITY;
  }
  if (value === ".nan") {
    return Number.NaN;
  }
  const n = value.includes(":") ? sexagesimal(value, pythonFloat) : pythonFloat(value);
  return n === undefined ? undefined : sign * n;
}

/** `construct` refused: PyYAML raises, which `load_untrusted_yaml` refuses as a construct it cannot load. */
function constructed<T>(value: T | undefined, data: string, tag: string): T {
  if (value === undefined) {
    throw new Error(`cannot construct ${tag} from ${pyRepr(data)}`);
  }
  return value;
}

const pyyamlIntType = new YamlType(INT_TAG, {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_INT_RESOLVE_RE.test(data),
  construct: (data: string) => constructed(pyyamlConstructInt(data), data, "int"),
});

const pyyamlFloatType = new YamlType(FLOAT_TAG, {
  kind: "scalar",
  resolve: (data: unknown) => typeof data === "string" && PYYAML_FLOAT_RESOLVE_RE.test(data),
  construct: (data: string) => constructed(pyyamlConstructFloat(data), data, "float"),
});

/** A tag the file writes on a scalar of any style (`!!int "10"`, `!!bool 'on'`, `!!null x`):
 * `SafeLoader` constructs the text by that tag, never by its implicit resolvers, and refuses text the
 * constructor cannot read. js-yaml looks an explicit tag up among these, after the implicit types of
 * the same tag. */
const PYYAML_EXPLICIT_SCALAR_TYPES = [
  new YamlType(NULL_TAG, {
    kind: "scalar",
    resolve: () => true,
    construct: () => null,
  }),
  new YamlType(BOOL_TAG, {
    kind: "scalar",
    resolve: (data: unknown) => typeof data === "string",
    construct: pyyamlConstructBool,
  }),
  new YamlType(INT_TAG, {
    kind: "scalar",
    resolve: (data: unknown) => typeof data === "string",
    construct: (data: string) => constructed(pyyamlConstructInt(data), data, "int"),
  }),
  new YamlType(FLOAT_TAG, {
    kind: "scalar",
    resolve: (data: unknown) => typeof data === "string",
    construct: (data: string) => constructed(pyyamlConstructFloat(data), data, "float"),
  }),
  new YamlType(TIMESTAMP_TAG, {
    kind: "scalar",
    // An empty `!!timestamp` node is the empty text, which no date matches.
    resolve: (data: unknown) => data === null || typeof data === "string",
    construct: (data: string | null) => pyyamlConstructTimestamp(data ?? ""),
  }),
  // `!!merge foo` is a merge key whatever its text, as SafeLoader's flatten_mapping reads the tag alone.
  new YamlType(MERGE_TAG, {
    kind: "scalar",
    resolve: () => true,
    construct: () => PYYAML_MERGE_VALUE,
  }),
];

/** A `<<` merge scalar read anywhere but as a mapping key. */
const PYYAML_MERGE_VALUE = Object.freeze({ pyyamlMergeValue: true });

/** Whether `value` holds {@link PYYAML_MERGE_VALUE}; each shared (aliased) node is visited once. */
function holdsMergeValue(value: unknown, seen = new Set<object>()): boolean {
  if (value === PYYAML_MERGE_VALUE) {
    return true;
  }
  if (typeof value !== "object" || value === null || seen.has(value)) {
    return false;
  }
  seen.add(value);
  return Object.values(value).some((child) => holdsMergeValue(child, seen));
}

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
    defaultImplicit(NULL_TAG),
    pyyamlBoolType,
    pyyamlIntType,
    pyyamlFloatType,
    pyyamlTimestampType,
    // `<<: *anchor` merge keys (PyYAML's `SafeLoader` resolves and flattens these by default via
    // `Resolver`/`SafeConstructor.flatten_mapping`, confirmed this item) -- reused from
    // `DEFAULT_SCHEMA` like the `null` type above, since js-yaml's own merge-key handling in
    // `storeMappingPair` triggers purely on this tag, keyed on the exact same `<<` scalar PyYAML
    // matches; an explicit key still overrides a merged-in one, since js-yaml applies the merged
    // pairs before the node's own explicit pairs, matching `flatten_mapping`'s ordering. Read as a
    // value, the scalar is a marker {@link loadPyyaml} refuses, as SafeLoader has no constructor for it.
    new YamlType(MERGE_TAG, {
      kind: "scalar",
      resolve: defaultImplicit(MERGE_TAG).resolve,
      construct: () => PYYAML_MERGE_VALUE,
    }),
    // YAML 1.1's `=` value type: PyYAML resolves a plain `=` to it and SafeLoader cannot construct it.
    new YamlType(VALUE_TAG, {
      kind: "scalar",
      resolve: (data: unknown) => data === "=",
      construct: () => {
        throw new Error(`could not determine a constructor for the tag '${VALUE_TAG}'`);
      },
    }),
  ],
  explicit: PYYAML_EXPLICIT_SCALAR_TYPES,
});

/** PyYAML's `construct_yaml_timestamp` (`yaml/constructor.py:322-351`) plus Python's own
 * `str(date)`/`str(datetime)` rendering, given a {@link PyyamlTimestamp} marker's raw text -- never a
 * plain string, which {@link normalizeDeviationDates} passes through completely unchanged. Performs
 * **no range validation**: an out-of-range field renders anyway (a syntactically-plausible but
 * semantically invalid string, which the loader keeps as text); {@link parseDate} range-checks a
 * quoted and a pythonized-unquoted value alike. */
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

/** js-yaml's composer state as its `listener` option sees each node close (js-yaml 4.3.2 `lib/loader.js`). */
interface ComposerState {
  readonly input: string;
  readonly position: number;
  tag: string | null;
  readonly anchor: string | null;
  kind: string | null;
  result: unknown;
  readonly implicitTypes: CompiledType[];
  readonly anchorMap: Record<string, unknown>;
}

/** Where the next character in `text` from `from` (before `end`) sits that is not a space, a line break or in a comment.
 * Only called between nodes, where the YAML scanner reads every `#` as a comment to the end of its line, even right
 * after an indicator (`,#:` is a comma then a comment). */
function skipSeparation(text: string, from: number, end = text.length): number {
  let at = from;
  while (at < end) {
    const ch = text[at];
    if (ch === " " || ch === "\t" || ch === "\r" || ch === "\n") {
      at += 1;
    } else if (ch === "#") {
      const lineEnd = text.indexOf("\n", at);
      at = lineEnd === -1 || lineEnd > end ? end : lineEnd;
    } else {
      break;
    }
  }
  return at;
}

/** Whether `text` from `position` on, past spaces, line breaks and comments, opens with the `:` of a mapping value:
 * one followed by a space, a line break, the end, or a flow indicator, or written right after a flow collection. */
function valueIndicatorFollows(text: string, position: number): boolean {
  const at = skipSeparation(text, position);
  if (text[at] !== ":") {
    return false;
  }
  const after = text[at + 1];
  return (
    after === undefined ||
    /[\s,[\]{}]/.test(after) ||
    (at === position && (text[at - 1] === "]" || text[at - 1] === "}"))
  );
}

/** The last indicator in `text` between two sibling nodes (or a collection's start and its first node), past spaces,
 * line breaks and comments: `?` before an explicit key, `:` before a mapping value. Only separators, indicators and
 * comments sit between two nodes, so no quoted text can hide a `#` here. */
function lastIndicator(text: string, from: number, to: number): string {
  let last = "";
  for (let at = skipSeparation(text, from, to); at < to; at = skipSeparation(text, at + 1, to)) {
    last = text[at] ?? "";
  }
  return last;
}

/** A value js-yaml composed as a sequence or mapping (an alias to one included), which PyYAML cannot hash as a key. */
function isCollection(value: unknown): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    value !== PYYAML_MERGE_VALUE &&
    !isPyyamlTimestamp(value)
  );
}

/** A value PyYAML merges: a mapping, never a sequence (js-yaml would merge a sequence by its indexes). */
function isMergeSource(value: unknown): boolean {
  return isRecord(value) && isCollection(value);
}

function notMergeable(): YAMLException {
  return new YAMLException(
    "while constructing a mapping: expected a mapping or list of mappings for merging",
  );
}

/** PyYAML's merge for a `? <<` key js-yaml stored under its text: the mapping's own keys win, then the first source. */
function mergeExplicit(mapping: Record<string, unknown>, sources: unknown[]): void {
  delete mapping[String(PYYAML_MERGE_VALUE)];
  for (const source of sources) {
    for (const [key, value] of Object.entries(source as Record<string, unknown>)) {
      if (!Object.hasOwn(mapping, key)) {
        mapping[key] = value;
      }
    }
  }
}

function unhashableKey(): YAMLException {
  return new YAMLException("while constructing a mapping: found unhashable key");
}

/** Per open node, as {@link PyyamlComposer} reads it. */
interface OpenNode {
  /** The indicator written right before this node in its parent (see {@link lastIndicator}). */
  readonly before: string;
  /** Where the last node directly inside this one ended (this node's start until one has). */
  childEnd: number;
  /** Whether a merge key has closed directly inside this node, and its value is still to come (`pending`) or not. */
  merge: "none" | "pending" | "done";
  /** The sources of a merge written as an explicit block key (`? <<`), which js-yaml stores under the key's text as
   * it drops an explicit block key's tag (js-yaml 4.3.2 `readBlockMapping`); merged when this mapping closes. */
  explicitMergeSources?: unknown[];
  /** The sequences and mappings directly inside this node with no `:` before them: keys, if this node is a mapping
   * (js-yaml also opens a node around a whole collection, whose one child is its own result). */
  withoutValueIndicator?: unknown[];
}

/** The PyYAML node rules js-yaml's composer does not follow, applied as each node closes (its `listener` option):
 * a scalar tagged `!` resolves as if untagged whatever its quoting; a sequence or mapping read as a mapping key is
 * refused, as PyYAML cannot hash it (js-yaml would name it by its text); a merge value that is not a mapping or a
 * sequence of mappings is refused; with `uniqueKeys`, a mapping that writes `<<` twice is refused. A node is a key
 * from where it sits among its siblings: after `?`, before `:`, or in a mapping with no `:` before it. */
class PyyamlComposer {
  private readonly open: OpenNode[] = [];

  constructor(private readonly uniqueKeys: boolean) {}

  readonly listener = (event: string, composer: unknown): void => {
    const state = composer as ComposerState;
    const parent = this.open.at(-1);
    if (event === "open") {
      this.open.push({
        before:
          parent === undefined ? "" : lastIndicator(state.input, parent.childEnd, state.position),
        childEnd: state.position,
        merge: "none",
      });
      return;
    }
    const node = this.open.pop() as OpenNode;
    const container = this.open.at(-1);
    if (state.tag === "!" && state.kind === "scalar" && typeof state.result === "string") {
      resolveNonSpecific(state);
    }
    const result = state.result;
    if (state.kind === "mapping") {
      if (node.withoutValueIndicator?.some((child) => child !== result)) {
        throw unhashableKey();
      }
      if (node.merge === "pending") {
        throw notMergeable();
      }
      if (node.explicitMergeSources !== undefined && isRecord(result)) {
        mergeExplicit(result, node.explicitMergeSources);
      }
    }
    if (container === undefined) {
      return;
    }
    container.childEnd = state.position;
    if (container.merge === "pending") {
      container.merge = "done";
      if (!(isMergeSource(result) || (Array.isArray(result) && result.every(isMergeSource)))) {
        throw notMergeable();
      }
      if (container.explicitMergeSources !== undefined) {
        container.explicitMergeSources = Array.isArray(result) ? result : [result];
      }
      return;
    }
    // js-yaml opens an untagged node around an explicit block key, so a merge key can close inside one.
    if (state.tag === MERGE_TAG || (state.kind === "scalar" && node.merge === "pending")) {
      if (container.merge === "done" && this.uniqueKeys) {
        // `<<` is a key too: which of two merges wins is never read (18.184k D1).
        throw new YAMLException("while constructing a mapping: found the merge key '<<' twice");
      }
      container.merge = "pending";
      if (node.before === "?") {
        container.explicitMergeSources = [];
      }
      return;
    }
    if (!isCollection(result)) {
      return;
    }
    if (node.before === "?" || valueIndicatorFollows(state.input, state.position)) {
      throw unhashableKey();
    }
    if (node.before !== ":") {
      container.withoutValueIndicator ??= [];
      container.withoutValueIndicator.push(result);
    }
  };
}

/** PyYAML resolves a `!` scalar by the implicit resolvers; their regexes end in Python's `$`, which also matches
 * before one trailing newline (a `! |` block), while the constructors read the whole text. */
function resolveNonSpecific(state: ComposerState): void {
  const text = state.result as string;
  const resolved = text.endsWith("\n") ? text.slice(0, -1) : text;
  // PyYAML's null resolver also takes empty text, which js-yaml's null type leaves to an empty node.
  const type = state.implicitTypes.find((candidate) =>
    candidate.resolve(resolved === "" && candidate.tag === NULL_TAG ? null : resolved),
  );
  if (type !== undefined) {
    state.result = type.construct(text);
  }
  // A merge key keeps its tag for the mapping; any other tag is cleared, so no enclosing close constructs it again.
  state.tag = type?.tag === MERGE_TAG ? MERGE_TAG : null;
  if (state.anchor !== null) {
    state.anchorMap[state.anchor] = state.result;
  }
}

/** `text` read with {@link PYYAML_SAFE_SCHEMA}: every load with that schema goes through here, so a `<<`
 * read as a value is refused as SafeLoader refuses it, and {@link PyyamlComposer}'s node rules apply. A repeated key
 * keeps its last value; with `uniqueKeys` it is refused, `<<` included. */
export function pyyamlLoad(text: string, uniqueKeys = false): unknown {
  const composer = new PyyamlComposer(uniqueKeys);
  const data = yamlLoad(text, {
    schema: PYYAML_SAFE_SCHEMA,
    json: !uniqueKeys,
    listener: composer.listener,
  });
  if (holdsMergeValue(data)) {
    throw new YAMLException(
      "could not determine a constructor for the tag 'tag:yaml.org,2002:merge'",
    );
  }
  return data;
}

/** The YAML at `path` as Python's `load_untrusted_yaml` reads it (PyYAML's `SafeLoader` typing, PYYAML_SAFE_SCHEMA),
 * refusing a file that is not UTF-8, nests too deeply, or carries YAML it cannot load under `key`. With `uniqueKeys`, a
 * mapping that writes one key twice is refused too (js-yaml's own duplicate-key error); without it the last value wins,
 * as `SafeLoader` keeps it (`json: true`). */
export function loadPyyaml(path: string, key: string, what: string, uniqueKeys = false): unknown {
  // Python passes the loader a `Path`, so its errors name `./r.yaml` as `r.yaml`.
  const where = `${what} at ${pyRepr(normalizePosixPath(path))}`;
  let text: string;
  try {
    // A fatal decode, as Python's `read_text(encoding="utf-8")`: a lossy one would turn a 0xff byte
    // into U+FFFD and read the mangled text instead of refusing the file.
    text = decodeUtf8Strict(readFileSync(path));
  } catch (error) {
    throw new InputError(
      key,
      `${where} is not valid UTF-8: ${(error as Error).message}.`,
      `save ${what} as UTF-8 text.`,
    );
  }
  try {
    return pyyamlLoad(text, uniqueKeys);
  } catch (error) {
    if (error instanceof RangeError || /exceeded maxDepth/.test((error as Error).message)) {
      // js-yaml caps nesting (and recurses per level), as PyYAML's composer hits its RecursionError.
      throw new InputError(
        key,
        `${where} is nested too deeply to parse safely.`,
        `flatten ${what}'s structure; it exceeds the engine's safe nesting depth.`,
      );
    }
    throw new InputError(
      key,
      `${where} carries a YAML construct the engine refuses to load: ${(error as Error).message}.`,
      `remove custom tags and aliases from ${what}; only plain YAML scalars, mappings, and sequences are accepted.`,
    );
  }
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
  const parsed = loadPyyaml(path, "input.deviation_invalid", "the deviation register");
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

/** The text of one report file, or `undefined` when the report folder has no entry by that name,
 * ported from `_read_report_file`. An entry that is there but cannot be read as a regular file (a
 * folder, a dangling link, a link into a folder that can be listed but not entered, a file without
 * read permission) throws: readiness reports it as a blocking reason rather than reading it as
 * absent, which would let a coverage gap or an integrity break stop blocking. */
function readReportFile(path: string): string | undefined {
  if (lstatSync(path, { throwIfNoEntry: false }) === undefined) {
    return undefined;
  }
  if (!statSync(path).isFile()) {
    throw new Error(`${path} is not a regular file`);
  }
  return readFileSync(path, "utf-8");
}

function parseJsonl(text: string | undefined): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  for (const rawLine of (text ?? "").split("\n")) {
    const line = rawLine.trim();
    if (line) {
      out.push(JSON.parse(line));
    }
  }
  return out;
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

  // A stage-4 input every report has: an absent one is a blocking reason, never an empty file, so
  // deleting the record of a gap or a break cannot clear it (SPEC §13.3.4).
  const reportFile = (name: string): string | undefined => {
    let text: string | undefined;
    try {
      text = readReportFile(joinPath(reportDir, name));
    } catch {
      reasons.push(`cannot read report file ${name}`);
      return undefined;
    }
    if (text === undefined) {
      reasons.push(`missing report file ${name}`);
    }
    return text;
  };

  for (const record of parseJsonl(reportFile("integrity.jsonl"))) {
    const status = record.status;
    if (typeof status === "string" && GAP_INTEGRITY_STATUSES.has(status)) {
      reasons.push(`integrity ${pyStr(status)} on stream ${pyStr(record.stream)}`);
    }
  }

  const coverageText = reportFile("coverage.json");
  const coverage: Record<string, unknown> =
    coverageText === undefined ? {} : JSON.parse(coverageText);
  const subjects = isRecord(coverage.subjects) ? coverage.subjects : {};
  for (const subject of Object.keys(subjects).sort(byteCompare)) {
    const entry = subjects[subject];
    if (!isRecord(entry)) {
      continue;
    }
    if (entry.coverage_status === "gap") {
      reasons.push(`coverage shortfall for subject ${subject}`);
    } else if (entry.coverage_status === "unknown") {
      // No independent denominator reconciles the subject's records (SPEC §6.5, §13.3.4).
      reasons.push(`coverage unknown for subject ${subject}: no independent denominator`);
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

  for (const statement of parseJsonl(reportFile("applicability.jsonl"))) {
    const driftRaw = pyGet(statement, "drift", []);
    const drift = pyTruthy(driftRaw) && Array.isArray(driftRaw) ? driftRaw : [];
    for (const entry of drift) {
      const d = isRecord(entry) ? entry : {};
      reasons.push(`applicability drift: ${pyStr(d.kind)} ${pyStr(d.ref)}`);
    }
  }

  const assertionsText = reportFile("assertions.json");
  const assertions: Record<string, unknown>[] =
    assertionsText === undefined ? [] : JSON.parse(assertionsText);
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
