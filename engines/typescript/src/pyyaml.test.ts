/** `safeDump` reproduces the bytes PyYAML 6's `yaml.safe_dump(value, sort_keys=False)` writes. */

import assert from "node:assert/strict";
import { test } from "node:test";
import { safeDump } from "./pyyaml";

// Each expected string was produced once by PyYAML 6.0.3 (`yaml.safe_dump(value, sort_keys=False)`).
// A string case dumps `{ value: s, "as key": { [s]: "v" } }`, so it covers both the value and the
// simple-key paths.
const CASES: [string, unknown, string][] = [
  [
    "plain word",
    { value: "deployer", "as key": { deployer: "v" } },
    "value: deployer\nas key:\n  deployer: v\n",
  ],
  [
    "plain with spaces",
    { value: "Agent records", "as key": { "Agent records": "v" } },
    "value: Agent records\nas key:\n  Agent records: v\n",
  ],
  ["empty string", { value: "", "as key": { "": "v" } }, "value: ''\nas key:\n  ? ''\n  : v\n"],
  ["bool yes", { value: "yes", "as key": { yes: "v" } }, "value: 'yes'\nas key:\n  'yes': v\n"],
  ["bool No", { value: "No", "as key": { No: "v" } }, "value: 'No'\nas key:\n  'No': v\n"],
  ["bool ON", { value: "ON", "as key": { ON: "v" } }, "value: 'ON'\nas key:\n  'ON': v\n"],
  ["bool off", { value: "off", "as key": { off: "v" } }, "value: 'off'\nas key:\n  'off': v\n"],
  [
    "bool True",
    { value: "True", "as key": { True: "v" } },
    "value: 'True'\nas key:\n  'True': v\n",
  ],
  [
    "bool FALSE",
    { value: "FALSE", "as key": { FALSE: "v" } },
    "value: 'FALSE'\nas key:\n  'FALSE': v\n",
  ],
  ["bare y is a string", { value: "y", "as key": { y: "v" } }, "value: y\nas key:\n  y: v\n"],
  [
    "null word",
    { value: "null", "as key": { null: "v" } },
    "value: 'null'\nas key:\n  'null': v\n",
  ],
  [
    "Null word",
    { value: "Null", "as key": { Null: "v" } },
    "value: 'Null'\nas key:\n  'Null': v\n",
  ],
  ["tilde", { value: "~", "as key": { "~": "v" } }, "value: '~'\nas key:\n  '~': v\n"],
  ["int decimal", { value: "42", "as key": { "42": "v" } }, "value: '42'\nas key:\n  '42': v\n"],
  ["int signed", { value: "-7", "as key": { "-7": "v" } }, "value: '-7'\nas key:\n  '-7': v\n"],
  [
    "int binary",
    { value: "0b1010", "as key": { "0b1010": "v" } },
    "value: '0b1010'\nas key:\n  '0b1010': v\n",
  ],
  [
    "int octal leading 0",
    { value: "017", "as key": { "017": "v" } },
    "value: '017'\nas key:\n  '017': v\n",
  ],
  [
    "0o is not an int",
    { value: "0o17", "as key": { "0o17": "v" } },
    "value: 0o17\nas key:\n  0o17: v\n",
  ],
  [
    "int hex",
    { value: "0x1F", "as key": { "0x1F": "v" } },
    "value: '0x1F'\nas key:\n  '0x1F': v\n",
  ],
  [
    "int sexagesimal",
    { value: "190:20:30", "as key": { "190:20:30": "v" } },
    "value: '190:20:30'\nas key:\n  '190:20:30': v\n",
  ],
  [
    "int underscores",
    { value: "1_000", "as key": { "1_000": "v" } },
    "value: '1_000'\nas key:\n  '1_000': v\n",
  ],
  ["float", { value: "1.5", "as key": { "1.5": "v" } }, "value: '1.5'\nas key:\n  '1.5': v\n"],
  [
    "float exponent",
    { value: "1.0e+3", "as key": { "1.0e+3": "v" } },
    "value: '1.0e+3'\nas key:\n  '1.0e+3': v\n",
  ],
  [
    "exponent needs sign",
    { value: "1e3", "as key": { "1e3": "v" } },
    "value: 1e3\nas key:\n  1e3: v\n",
  ],
  [
    "float sexagesimal",
    { value: "20:30.15", "as key": { "20:30.15": "v" } },
    "value: '20:30.15'\nas key:\n  '20:30.15': v\n",
  ],
  [
    "float .inf",
    { value: ".inf", "as key": { ".inf": "v" } },
    "value: '.inf'\nas key:\n  '.inf': v\n",
  ],
  [
    "float -.Inf",
    { value: "-.Inf", "as key": { "-.Inf": "v" } },
    "value: '-.Inf'\nas key:\n  '-.Inf': v\n",
  ],
  [
    "float .NaN",
    { value: ".NaN", "as key": { ".NaN": "v" } },
    "value: '.NaN'\nas key:\n  '.NaN': v\n",
  ],
  [
    "timestamp date",
    { value: "2026-01-01", "as key": { "2026-01-01": "v" } },
    "value: '2026-01-01'\nas key:\n  '2026-01-01': v\n",
  ],
  [
    "timestamp datetime",
    { value: "2026-01-01T00:00:00Z", "as key": { "2026-01-01T00:00:00Z": "v" } },
    "value: '2026-01-01T00:00:00Z'\nas key:\n  '2026-01-01T00:00:00Z': v\n",
  ],
  [
    "timestamp spaced",
    { value: "2001-12-14 21:59:43.10 -5", "as key": { "2001-12-14 21:59:43.10 -5": "v" } },
    "value: '2001-12-14 21:59:43.10 -5'\nas key:\n  '2001-12-14 21:59:43.10 -5': v\n",
  ],
  ["merge", { value: "<<", "as key": { "<<": "v" } }, "value: '<<'\nas key:\n  '<<': v\n"],
  ["value", { value: "=", "as key": { "=": "v" } }, "value: '='\nas key:\n  '=': v\n"],
  ["leading space", { value: " a", "as key": { " a": "v" } }, "value: ' a'\nas key:\n  ' a': v\n"],
  ["trailing space", { value: "a ", "as key": { "a ": "v" } }, "value: 'a '\nas key:\n  'a ': v\n"],
  [
    "colon space",
    { value: "a: b", "as key": { "a: b": "v" } },
    "value: 'a: b'\nas key:\n  'a: b': v\n",
  ],
  ["colon at end", { value: "a:", "as key": { "a:": "v" } }, "value: 'a:'\nas key:\n  'a:': v\n"],
  ["colon inside", { value: "a:b", "as key": { "a:b": "v" } }, "value: a:b\nas key:\n  a:b: v\n"],
  [
    "space hash",
    { value: "a #b", "as key": { "a #b": "v" } },
    "value: 'a #b'\nas key:\n  'a #b': v\n",
  ],
  ["hash inside", { value: "a#b", "as key": { "a#b": "v" } }, "value: a#b\nas key:\n  a#b: v\n"],
  [
    "document start",
    { value: "---", "as key": { "---": "v" } },
    "value: '---'\nas key:\n  '---': v\n",
  ],
  [
    "document end",
    { value: "...", "as key": { "...": "v" } },
    "value: '...'\nas key:\n  '...': v\n",
  ],
  [
    "single quote inside",
    { value: "it's", "as key": { "it's": "v" } },
    "value: it's\nas key:\n  it's: v\n",
  ],
  [
    "double quote inside",
    { value: 'say "hi"', "as key": { 'say "hi"': "v" } },
    'value: say "hi"\nas key:\n  say "hi": v\n',
  ],
  ["tab", { value: "a\tb", "as key": { "a\tb": "v" } }, 'value: "a\\tb"\nas key:\n  "a\\tb": v\n'],
  [
    "newline",
    { value: "a\nb", "as key": { "a\nb": "v" } },
    "value: 'a\n\n  b'\nas key:\n  ? 'a\n\n    b'\n  : v\n",
  ],
  [
    "trailing newline",
    { value: "a\n", "as key": { "a\n": "v" } },
    "value: 'a\n\n  '\nas key:\n  ? 'a\n\n    '\n  : v\n",
  ],
  ["CR", { value: "a\rb", "as key": { "a\rb": "v" } }, 'value: "a\\rb"\nas key:\n  "a\\rb": v\n'],
  [
    "NEL",
    { value: "a\u0085b", "as key": { "a\u0085b": "v" } },
    'value: "a\\Nb"\nas key:\n  ? "a\\Nb"\n  : v\n',
  ],
  [
    "LS",
    { value: "a\u2028b", "as key": { "a\u2028b": "v" } },
    'value: "a\\Lb"\nas key:\n  ? "a\\Lb"\n  : v\n',
  ],
  [
    "PS",
    { value: "a\u2029b", "as key": { "a\u2029b": "v" } },
    'value: "a\\Pb"\nas key:\n  ? "a\\Pb"\n  : v\n',
  ],
  [
    "BEL",
    { value: "a\u0007b", "as key": { "a\u0007b": "v" } },
    'value: "a\\ab"\nas key:\n  "a\\ab": v\n',
  ],
  [
    "NUL",
    { value: "a\u0000b", "as key": { "a\u0000b": "v" } },
    'value: "a\\0b"\nas key:\n  "a\\0b": v\n',
  ],
  [
    "ESC",
    { value: "a\u001bb", "as key": { "a\u001bb": "v" } },
    'value: "a\\eb"\nas key:\n  "a\\eb": v\n',
  ],
  [
    "DEL",
    { value: "a\u007fb", "as key": { "a\u007fb": "v" } },
    'value: "a\\x7Fb"\nas key:\n  "a\\x7Fb": v\n',
  ],
  [
    "NBSP",
    { value: "a\u00a0b", "as key": { "a\u00a0b": "v" } },
    'value: "a\\_b"\nas key:\n  "a\\_b": v\n',
  ],
  [
    "BOM",
    { value: "\ufeffa", "as key": { "\ufeffa": "v" } },
    'value: "\\uFEFFa"\nas key:\n  "\\uFEFFa": v\n',
  ],
  [
    "e acute",
    { value: "caf\u00e9", "as key": { "caf\u00e9": "v" } },
    'value: "caf\\xE9"\nas key:\n  "caf\\xE9": v\n',
  ],
  [
    "CJK",
    { value: "\u4e2d\u6587", "as key": { "\u4e2d\u6587": "v" } },
    'value: "\\u4E2D\\u6587"\nas key:\n  "\\u4E2D\\u6587": v\n',
  ],
  [
    "emoji astral",
    { value: "ok \ud83d\ude00", "as key": { "ok \ud83d\ude00": "v" } },
    'value: "ok \\U0001F600"\nas key:\n  "ok \\U0001F600": v\n',
  ],
  [
    "lone surrogate",
    { value: "a\ud800b", "as key": { "a\ud800b": "v" } },
    'value: "a\\uD800b"\nas key:\n  "a\\uD800b": v\n',
  ],
  [
    "space then newline",
    { value: "a \nb", "as key": { "a \nb": "v" } },
    'value: "a \\nb"\nas key:\n  ? "a \\nb"\n  : v\n',
  ],
  [
    "newline then space",
    { value: "a\n b", "as key": { "a\n b": "v" } },
    'value: "a\\n b"\nas key:\n  ? "a\\n b"\n  : v\n',
  ],
  [
    "200 chars with spaces",
    {
      value:
        "word000 word001 word002 word003 word004 word005 word006 word007 word008 word009 word010 word011 word012 word013 word014 word015 word016 word017 word018 word019 word020 word021 word022 word023 word024",
      "as key": {
        "word000 word001 word002 word003 word004 word005 word006 word007 word008 word009 word010 word011 word012 word013 word014 word015 word016 word017 word018 word019 word020 word021 word022 word023 word024":
          "v",
      },
    },
    "value: word000 word001 word002 word003 word004 word005 word006 word007 word008 word009\n  word010 word011 word012 word013 word014 word015 word016 word017 word018 word019\n  word020 word021 word022 word023 word024\nas key:\n  ? word000 word001 word002 word003 word004 word005 word006 word007 word008 word009\n    word010 word011 word012 word013 word014 word015 word016 word017 word018 word019\n    word020 word021 word022 word023 word024\n  : v\n",
  ],
  [
    "200 chars without spaces",
    {
      value:
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "as key": {
        xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx:
          "v",
      },
    },
    "value: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nas key:\n  ? xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n  : v\n",
  ],
  [
    "200 chars with spaces and a quote",
    {
      value:
        "it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's ",
      "as key": {
        "it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's it's ":
          "v",
      },
    },
    "value: 'it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s\n  it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s\n  it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s '\nas key:\n  ? 'it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s\n    it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s\n    it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s it''s\n    it''s '\n  : v\n",
  ],
  [
    "200 chars with spaces and a tab",
    {
      value:
        "ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab",
      "as key": {
        "ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab\tcd ef ab":
          "v",
      },
    },
    'value: "ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\t\\\n  cd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\t\\\n  cd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab"\nas key:\n  ? "ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd\\\n    \\ ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\t\\\n    cd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab\\tcd ef ab"\n  : v\n',
  ],
  [
    "200 chars non-ASCII",
    {
      value:
        "\u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 ",
      "as key": {
        "\u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 \u00e9t\u00e9 ":
          "v",
      },
    },
    'value: "\\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9\\\n  t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9\\\n  t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9\\\n  t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9\\\n  t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9\\\n  t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9\\\n  t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 "\nas key:\n  ? "\\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9\\\n    \\ \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9\\\n    \\ \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9\\\n    \\ \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9\\\n    \\ \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9\\\n    \\ \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9 \\xE9t\\xE9\\\n    \\ \\xE9t\\xE9 \\xE9t\\xE9 "\n  : v\n',
  ],
  [
    "double spaces at fold",
    {
      value:
        "abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  ab",
      "as key": {
        "abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  ab":
          "v",
      },
    },
    "value: abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  ab\nas key:\n  ? abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  abcdefghi  ab\n  : v\n",
  ],
  ["leading -", { value: "-x", "as key": { "-x": "v" } }, "value: -x\nas key:\n  -x: v\n"],
  ["leading ?", { value: "?x", "as key": { "?x": "v" } }, "value: ?x\nas key:\n  ?x: v\n"],
  ["leading :", { value: ":x", "as key": { ":x": "v" } }, "value: :x\nas key:\n  :x: v\n"],
  ["leading ,", { value: ",x", "as key": { ",x": "v" } }, "value: ',x'\nas key:\n  ',x': v\n"],
  ["leading [", { value: "[x", "as key": { "[x": "v" } }, "value: '[x'\nas key:\n  '[x': v\n"],
  ["leading ]", { value: "]x", "as key": { "]x": "v" } }, "value: ']x'\nas key:\n  ']x': v\n"],
  ["leading {", { value: "{x", "as key": { "{x": "v" } }, "value: '{x'\nas key:\n  '{x': v\n"],
  ["leading }", { value: "}x", "as key": { "}x": "v" } }, "value: '}x'\nas key:\n  '}x': v\n"],
  ["leading #", { value: "#x", "as key": { "#x": "v" } }, "value: '#x'\nas key:\n  '#x': v\n"],
  ["leading &", { value: "&x", "as key": { "&x": "v" } }, "value: '&x'\nas key:\n  '&x': v\n"],
  ["leading *", { value: "*x", "as key": { "*x": "v" } }, "value: '*x'\nas key:\n  '*x': v\n"],
  ["leading !", { value: "!x", "as key": { "!x": "v" } }, "value: '!x'\nas key:\n  '!x': v\n"],
  ["leading |", { value: "|x", "as key": { "|x": "v" } }, "value: '|x'\nas key:\n  '|x': v\n"],
  ["leading >", { value: ">x", "as key": { ">x": "v" } }, "value: '>x'\nas key:\n  '>x': v\n"],
  ["leading '", { value: "'x", "as key": { "'x": "v" } }, "value: '''x'\nas key:\n  '''x': v\n"],
  ['leading "', { value: '"x', "as key": { '"x': "v" } }, "value: '\"x'\nas key:\n  '\"x': v\n"],
  ["leading %", { value: "%x", "as key": { "%x": "v" } }, "value: '%x'\nas key:\n  '%x': v\n"],
  ["leading @", { value: "@x", "as key": { "@x": "v" } }, "value: '@x'\nas key:\n  '@x': v\n"],
  ["leading `", { value: "`x", "as key": { "`x": "v" } }, "value: '`x'\nas key:\n  '`x': v\n"],
  [
    "leading - space",
    { value: "- x", "as key": { "- x": "v" } },
    "value: '- x'\nas key:\n  '- x': v\n",
  ],
  [
    "leading ? space",
    { value: "? x", "as key": { "? x": "v" } },
    "value: '? x'\nas key:\n  '? x': v\n",
  ],
  [
    "leading : space",
    { value: ": x", "as key": { ": x": "v" } },
    "value: ': x'\nas key:\n  ': x': v\n",
  ],
  [
    "key of 122 code points stays simple",
    {
      kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk: 1,
    },
    "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk: 1\n",
  ],
  [
    "key of 123 code points goes after ?",
    {
      kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk: 1,
    },
    "? kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk\n: 1\n",
  ],
  ["multiline key goes after ?", { "a\nb": 1 }, "? 'a\n\n  b'\n: 1\n"],
  ["empty key goes after ?", { "": "x" }, "? ''\n: x\n"],
  [
    "scalars",
    { int: 1, neg: -12, big: 9007199254740991, t: true, f: false, n: null },
    "int: 1\nneg: -12\nbig: 9007199254740991\nt: true\nf: false\nn: null\n",
  ],
  [
    "empty list and mapping",
    { l: [], m: {}, nested: [[], {}] },
    "l: []\nm: {}\nnested:\n- []\n- {}\n",
  ],
  [
    "nested mappings and sequences",
    { a: { b: { c: [1, [2, 3], { d: "e", f: [{ g: null }] }] } }, h: [{ i: 1, j: 2 }, "k"] },
    "a:\n  b:\n    c:\n    - 1\n    - - 2\n      - 3\n    - d: e\n      f:\n      - g: null\nh:\n- i: 1\n  j: 2\n- k\n",
  ],
  ["top-level list", ["a", ["b", "c"], { d: 1 }], "- a\n- - b\n  - c\n- d: 1\n"],
  ["top-level plain scalar is open-ended", "plain", "plain\n...\n"],
  ["top-level quoted scalar", "yes", "'yes'\n"],
  ["top-level empty mapping", {}, "{}\n"],
];

for (const [name, value, expected] of CASES) {
  test(`safeDump: ${name}`, () => {
    assert.equal(safeDump(value), expected);
  });
}

test("safeDump: a list met twice gets &id001, then *id001", () => {
  const shared = ["a", "b"];
  assert.equal(
    safeDump({ first: shared, second: shared, nested: [shared, { again: shared }] }),
    "first: &id001\n- a\n- b\nsecond: *id001\nnested:\n- *id001\n- again: *id001\n",
  );
});

test("safeDump: an anchored sequence item puts its block on the next line", () => {
  const seq = [{ x: 1 }];
  assert.equal(
    safeDump({ items: [seq, seq], m: { k: seq } }),
    "items:\n- &id001\n  - x: 1\n- *id001\nm:\n  k: *id001\n",
  );
});

test("safeDump: a derived profile whose subjects share one evidence_sources list", () => {
  const evidenceSources = [
    {
      adapter: "otel-genai",
      source: "traces/agent.jsonl",
      class: "self_reported",
      class_justification:
        "read from the agent's own trace export; the agent could have written anything in it, so it is recorded as self-reported.",
    },
  ];
  const profile = {
    profile_version: 1,
    observation_window: { start: "2026-01-01T00:00:00Z", end: "2026-03-31T23:59:59Z" },
    pilot_window: true,
    catalogs: ["baseline@2026.09"],
    subjects: [
      {
        id: "agent-records",
        name: "Agent records",
        role: "deployer",
        evidence_sources: evidenceSources,
        declared_tools: ["search_docs", "send_email"],
        declared_models: ["gpt-4o-2024-08-06"],
      },
      {
        id: "support-bot",
        name: "Support bot",
        role: "deployer",
        evidence_sources: evidenceSources,
        declared_tools: [],
        declared_models: ["claude-3-5-sonnet"],
      },
    ],
  };
  assert.equal(
    safeDump(profile),
    "profile_version: 1\nobservation_window:\n  start: '2026-01-01T00:00:00Z'\n  end: '2026-03-31T23:59:59Z'\npilot_window: true\ncatalogs:\n- baseline@2026.09\nsubjects:\n- id: agent-records\n  name: Agent records\n  role: deployer\n  evidence_sources: &id001\n  - adapter: otel-genai\n    source: traces/agent.jsonl\n    class: self_reported\n    class_justification: read from the agent's own trace export; the agent could have\n      written anything in it, so it is recorded as self-reported.\n  declared_tools:\n  - search_docs\n  - send_email\n  declared_models:\n  - gpt-4o-2024-08-06\n- id: support-bot\n  name: Support bot\n  role: deployer\n  evidence_sources: *id001\n  declared_tools: []\n  declared_models:\n  - claude-3-5-sonnet\n",
  );
});

test("safeDump: equal but distinct lists are not aliased", () => {
  assert.equal(safeDump({ a: ["x"], b: ["x"] }), "a:\n- x\nb:\n- x\n");
});

test("safeDump: refuses values PyYAML's safe profile shape does not hold", () => {
  for (const bad of [1.5, Number.NaN, 2 ** 53, undefined, () => 1, Symbol("s"), 1n, new Date(0)]) {
    assert.throws(() => safeDump({ k: bad }), TypeError);
  }
});
