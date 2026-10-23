---
title: JSON profile
description: 'Spec refs: SPEC §8.7 (security of the engine itself)'
---

Spec refs: SPEC §8.7 (security of the engine itself)

AgentCE reads every JSON and JSON Lines file under one JSON profile, the same in the Python,
TypeScript and Java engines. Each engine has one reader that checks a file against the profile before
anything uses it, so a bundle, a report file or `state.json` gives the same result whichever engine
reads it.

The profile covers the bundle manifest, records and event streams, `assertions.json` and the report
files that readiness, `sign`, `report --validate` and `diff` read, `state.json`, coverage denominator
manifests, conformance manifests and message catalogues.

## What a file may hold

- JSON text (RFC 8259) in UTF-8. The reader ignores one leading byte-order mark, since some Windows
  tools write one. A report file the engine wrote is the exception, and the reader refuses a mark there.
- One value per document. In JSON Lines, one object per line, with lines split on LF only. A CRLF
  pair reads as LF. The reader skips a blank line, empty or holding only spaces and tabs.
- Object keys written once per object.
- Containers nested at most 1000 deep.
- Integers within the engine's digit limit.

## What puts a file outside the profile

| Rule | Example | Write it as |
|---|---|---|
| `invalid_utf8` | a byte that is not UTF-8, or an encoded surrogate | UTF-8 text |
| `lone_surrogate` | `"\ud800"` | a full surrogate pair, or the character itself |
| `duplicate_key` | `{"a": 1, "a": 2}` | each key once |
| `nan`, `infinity` | `NaN`, `Infinity`, `-Infinity` | a number, or `null` |
| `comment` | `// note` or `/* note */` | no comment |
| `trailing_comma` | `[1, 2,]` | `[1, 2]` |
| `leading_plus`, `leading_zero` | `+1`, `01` | `1` |
| `extra_value` | two values in one document | one value per document |
| `jsonl_line_split` | two values on one line, joined by a CR or U+2028 | one object per LF-ended line |
| `depth_over_limit` | containers nested 1001 deep | at most 1000 levels |
| `integer_digit_limit` | an integer longer than the digit limit | a shorter integer, or a string |

## Numbers

A number written without a fraction or exponent is an integer; one written with either is a float. A
field that holds a count refuses the float form, so `"rung": 2.0` is refused where `"rung": 2` reads.
Two numbers compare by value (`42.0` equals `42`), and values of different JSON types never compare
equal (`1` is not `true`, and `"1"` is not `1`).

Where a number appears in a message or a report, every engine writes it the same way: an integer as
its digits, a float as the shortest decimal that reads back to the same value, with `.0` when it is
whole (`5.0`), in exponent form at 1e16 and above or below 1e-4 (`1e+20`), and `-0.0` for negative
zero.

## What you see

The engine refuses a file outside the profile with exit code `3`, under the key it already uses for
that file when it cannot read it. The message names the rule and the line. For `report --from` on an
`assertions.json` that repeats a key:

```text
error [input.from_unreadable]: the assertions file 'assertions.json' is outside the JSON profile: duplicate_key at line 1 (SPEC 8.7).
  fix: re-run the command that wrote it, or correct the JSON at that line.
```

In a records folder, one JSON Lines record outside the profile does not stop the run. The engine lists
that line as not read, with the rule and its line in the file, and reads the other lines.

The [error catalogue](/explanation/errors/) lists the keys.

## Tests

One shared case table, [`spec/report/json-cases.json`](https://github.com/agent-conformance/agentce/blob/main/spec/report/json-cases.json), holds the
rules, the number rendering cases and the comparison cases. Each engine's tests read that table, so a
case added there runs in all three engines, and `tools/json_parity_check.py` checks that the three
engines give the same answer for every case.
