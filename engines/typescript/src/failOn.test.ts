/**
 * `parseFailOn` against the Python reference (`agentce/fail_on.py`): every expected cause below is
 * copied from the output of Python's `parse_fail_on` for the same expression.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { type Assertion, makeAssertion } from "./assertions";
import { InputError } from "./errors";
import { parseFailOn } from "./failOn";

const FIX =
  'use comparisons of the form field=="literal" joined by and/or, over: control, family, mode, outcome, rung, severity, subject.';
const FIELDS = "control, family, mode, outcome, rung, severity, subject";

function assertion(fields: Partial<Assertion>): Assertion {
  return makeAssertion({
    control: "AUV-01",
    controlVersion: "1",
    subject: "spiffe://corp/agents/a",
    outcome: "non-conformant",
    rung: 2,
    mode: "automated",
    window: ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
    population: [1, 1],
    severity: "high",
    family: "AUV",
    ...fields,
  });
}

const HIGH = assertion({});
const LOW = assertion({ control: "AUV-04", severity: "low", outcome: "partial", rung: 0 });

function refusal(expr: string): InputError {
  try {
    parseFailOn(expr);
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    return exc;
  }
  assert.fail(`${JSON.stringify(expr)} was accepted`);
}

test("parseFailOn accepts what Python accepts", () => {
  const cases: Array<[string, boolean[]]> = [
    ['outcome=="non-conformant" and severity=="high"', [true, false]],
    ['severity=="critical"', [false, false]],
    ['control=="AUV-04" or control=="AUV-01" and severity=="low"', [false, true]],
    [
      'control=="AUV-01" and severity=="low" or control=="AUV-04" and severity=="low"',
      [false, true],
    ],
    ['rung=="0" and mode=="automated" and family=="AUV"', [false, true]],
    ['rung=="2"', [true, false]],
    ['rung=="2.0"', [false, false]],
    ["control=='AUV-0\\4' and subject=='spiffe://corp/agents/a'", [false, true]],
    ['outcome=="partial"', [false, true]],
    ['control=="é"', [false, false]],
    ['control=="😀"', [false, false]],
    ['control=="AUV-01" or family=="AUV"', [true, true]],
  ];
  for (const [expr, expected] of cases) {
    const predicate = parseFailOn(expr);
    assert.deepEqual([HIGH, LOW].map(predicate), expected, expr);
  }
});

test("parseFailOn refuses with Python's cause", () => {
  const cases: Array<[string, string]> = [
    ["", "--fail-on '' is not a valid expression: the --fail-on expression is empty"],
    ["   ", "--fail-on '   ' is not a valid expression: the --fail-on expression is empty"],
    [
      'foo=="x"',
      `--fail-on 'foo=="x"' is not a valid expression: unknown field 'foo'; choose from: ${FIELDS}`,
    ],
    [
      'and=="x"',
      `--fail-on 'and=="x"' is not a valid expression: expected a field name, got the reserved word 'and'`,
    ],
    [
      'outcome "x"',
      `--fail-on 'outcome "x"' is not a valid expression: expected '==' after field name 'outcome'`,
    ],
    [
      'outcome="x"',
      `--fail-on 'outcome="x"' is not a valid expression: unexpected character '=' at position 7`,
    ],
    [
      "outcome==x",
      "--fail-on 'outcome==x' is not a valid expression: expected a quoted string literal after '=='",
    ],
    [
      'outcome=="x',
      `--fail-on 'outcome=="x' is not a valid expression: unterminated string literal starting at position 9`,
    ],
    [
      'outcome=="x" severity',
      `--fail-on 'outcome=="x" severity' is not a valid expression: unexpected token 'severity' after a complete expression`,
    ],
    [
      '__import__("os").system("id")',
      `--fail-on '__import__("os").system("id")' is not a valid expression: unexpected character '(' at position 10`,
    ],
    [
      "outcome==`id`",
      "--fail-on 'outcome==`id`' is not a valid expression: unexpected character '`' at position 9",
    ],
    [
      'outcome=="x" or os.system("id")',
      `--fail-on 'outcome=="x" or os.system("id")' is not a valid expression: unexpected character '.' at position 18`,
    ],
    [
      '(outcome=="x")',
      `--fail-on '(outcome=="x")' is not a valid expression: unexpected character '(' at position 0`,
    ],
    [
      'outcomé=="x"',
      `--fail-on 'outcomé=="x"' is not a valid expression: unknown field 'outcomé'; choose from: ${FIELDS}`,
    ],
    ["😀", "--fail-on '😀' is not a valid expression: unexpected character '😀' at position 0"],
    [
      'outcome=="x"\x01',
      `--fail-on 'outcome=="x"\\x01' is not a valid expression: unexpected character '\\x01' at position 12`,
    ],
    [
      "\u200b",
      "--fail-on '\\u200b' is not a valid expression: unexpected character '\\u200b' at position 0",
    ],
    // R19: the astral emoji is one code point, so \x01 sits at position 4, not 5.
    [
      '"😀" \x01',
      `--fail-on '"😀" \\x01' is not a valid expression: unexpected character '\\x01' at position 4`,
    ],
    [
      'outcome=="x" and',
      `--fail-on 'outcome=="x" and' is not a valid expression: expected a field name`,
    ],
    ["==", "--fail-on '==' is not a valid expression: expected a field name"],
    [
      "outcome==",
      "--fail-on 'outcome==' is not a valid expression: expected a quoted string literal after '=='",
    ],
    [
      '1outcome=="x"',
      `--fail-on '1outcome=="x"' is not a valid expression: unexpected character '1' at position 0`,
    ],
    [
      'a²=="x"',
      `--fail-on 'a²=="x"' is not a valid expression: unknown field 'a²'; choose from: ${FIELDS}`,
    ],
    [
      'outcome=="x\\',
      `--fail-on 'outcome=="x\\\\' is not a valid expression: unterminated string literal starting at position 9`,
    ],
    [
      "it's",
      `--fail-on "it's" is not a valid expression: unterminated string literal starting at position 2`,
    ],
    [
      'outcome==="x"',
      `--fail-on 'outcome==="x"' is not a valid expression: unexpected character '=' at position 9`,
    ],
    ['"x"=="x"', `--fail-on '"x"=="x"' is not a valid expression: expected a field name`],
    [
      'outcome=="\\U0001F600"\\U0001F600',
      `--fail-on 'outcome=="\\\\U0001F600"\\\\U0001F600' is not a valid expression: unexpected character '\\\\' at position 21`,
    ],
    [
      'Outcome=="x"',
      `--fail-on 'Outcome=="x"' is not a valid expression: unknown field 'Outcome'; choose from: ${FIELDS}`,
    ],
    [
      'outcome=="x" OR severity=="y"',
      `--fail-on 'outcome=="x" OR severity=="y"' is not a valid expression: unexpected token 'OR' after a complete expression`,
    ],
    ["-x", "--fail-on '-x' is not a valid expression: unexpected character '-' at position 0"],
  ];
  for (const [expr, cause] of cases) {
    const error = refusal(expr);
    assert.equal(error.key, "input.fail_on_invalid_expression", expr);
    assert.equal(error.cause, cause, expr);
    assert.equal(error.fix, FIX, expr);
  }
});

test("parseFailOn evaluates a 3000-clause chain", () => {
  const miss = Array<string>(3000).fill('control=="x"');
  assert.deepEqual([HIGH, LOW].map(parseFailOn(miss.join(" or "))), [false, false]);
  const hit = [...miss.slice(1), 'control=="AUV-01"'];
  assert.deepEqual([HIGH, LOW].map(parseFailOn(hit.join(" or "))), [true, false]);
  const all = Array<string>(3000).fill('family=="AUV"');
  assert.deepEqual([HIGH, LOW].map(parseFailOn(all.join(" and "))), [true, true]);
});

test("parseFailOn skips Python's whitespace", () => {
  const pyWhitespace = [
    0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001,
    0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
    0x205f, 0x3000,
  ];
  for (const cp of pyWhitespace) {
    const ws = String.fromCodePoint(cp);
    const predicate = parseFailOn(`${ws}severity${ws}==${ws}"high"${ws}`);
    assert.deepEqual([HIGH, LOW].map(predicate), [true, false], `U+${cp.toString(16)}`);
  }
  // F12 as Python ran it.
  assert.deepEqual([HIGH, LOW].map(parseFailOn('\x1c severity ==\u3000"high"\t\n\x85')), [
    true,
    false,
  ]);
  // JavaScript's \s takes U+FEFF; Python's isspace does not, so it is an unexpected character.
  assert.equal(
    refusal('\ufeffseverity=="high"').cause,
    "--fail-on '\\ufeffseverity==\"high\"' is not a valid expression: unexpected character '\\ufeff' at position 0",
  );
  assert.equal(
    refusal('severity=="high"\u180e').cause,
    "--fail-on 'severity==\"high\"\\u180e' is not a valid expression: unexpected character '\\u180e' at position 16",
  );
});
