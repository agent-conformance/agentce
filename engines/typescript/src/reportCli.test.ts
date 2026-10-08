/**
 * report on the shared argv scanner (18.110): the two grammar additions report needs (an option's
 * `choices`, refused at once with its own error, and an option's own needs-value key), and the public
 * conformance statement against Python's `render_public_statement` over the same assertions. The
 * expected statements are Python's output, pasted verbatim. `ReportCliTest.java` pins the same.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Grammar, scanArgv } from "./argv";
import { type Assertion, assertionFromJson } from "./assertions";
import { InputError } from "./errors";
import { appliedDeviationIds, renderPublicStatement } from "./report";

const G: Grammar = {
  options: [
    { names: ["-h", "--help"], kind: "help" },
    { names: ["--json"], kind: "flag" },
    { names: ["--from"], kind: "value", needsValueKey: "input.from_missing" },
    {
      names: ["--format"],
      kind: "value",
      choices: ["md", "public"],
      choiceError: (value) => new InputError("input.t_format", `bad format '${value}'.`, "FMT-FIX"),
    },
    { names: ["--out"], kind: "value" },
  ],
  maxPositionals: 0,
  unrecognizedKey: "input.t_unrecognized_flag",
  needsValueKey: "input.t_default_needs_value",
  needsValueCause: "plain",
  flagFix: "FLAG-FIX",
  extraArgFix: "EXTRA-FIX",
};

function refusal(argv: string[]): { key: string; cause: string; fix: string } {
  try {
    scanArgv(argv, G);
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    return { key: exc.key, cause: exc.cause, fix: exc.fix };
  }
  assert.fail(`no refusal for ${JSON.stringify(argv)}`);
}

const badFormat = (value: string) => ({
  key: "input.t_format",
  cause: `bad format '${value}'.`,
  fix: "FMT-FIX",
});

test("a choice outside the list is refused at once with its own error, in either spelling", () => {
  assert.deepEqual(refusal(["--format", "nope"]), badFormat("nope"));
  assert.deepEqual(refusal(["--format=nope"]), badFormat("nope"));
  assert.deepEqual(refusal(["--format="]), badFormat(""));
  assert.deepEqual(refusal(["--format", ""]), badFormat(""));
});

test("a bad choice is refused before a later unknown flag, extra argument or missing value", () => {
  assert.deepEqual(refusal(["--format", "nope", "--no-such-flag"]), badFormat("nope"));
  assert.deepEqual(refusal(["--format", "nope", "extra"]), badFormat("nope"));
  assert.deepEqual(refusal(["--format", "nope", "--from"]), badFormat("nope"));
  // An earlier missing value is still refused first: argv order.
  assert.equal(refusal(["--from", "--format", "nope"]).key, "input.from_missing");
});

test("a good choice is read, the last one wins, and a later bad one is refused", () => {
  assert.equal(scanArgv(["--format", "md", "--format=public"], G).values["--format"], "public");
  assert.deepEqual(refusal(["--format", "public", "--format", "nope"]), badFormat("nope"));
});

test("an option's own needs-value key, else the grammar's default, with Python's plain cause", () => {
  assert.deepEqual(refusal(["--from"]), {
    key: "input.from_missing",
    cause: "flag '--from' needs a value.",
    fix: "pass --from <value>.",
  });
  assert.deepEqual(refusal(["--out", "--json"]), {
    key: "input.t_default_needs_value",
    cause: "flag '--out' needs a value.",
    fix: "pass --out <value>.",
  });
  assert.equal(refusal(["--format"]).key, "input.t_default_needs_value");
});

test("a grammar without needsValueCause keeps argparse's own sentence (assess)", () => {
  const argparseCause: Grammar = { ...G, needsValueCause: undefined };
  try {
    scanArgv(["--from"], argparseCause);
    assert.fail("no refusal");
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    assert.equal(exc.key, "input.from_missing");
    assert.equal(exc.cause, "argument --from: expected one argument");
  }
});

test("a choice without its own error falls back to argparse's sentence and the default key", () => {
  const plain: Grammar = {
    ...G,
    options: [{ names: ["--role"], kind: "value", choices: ["provider", "deployer"] }],
  };
  try {
    scanArgv(["--role", "x"], plain);
    assert.fail("no refusal");
  } catch (exc) {
    assert.ok(exc instanceof InputError);
    assert.equal(exc.key, "input.t_unrecognized_flag");
    assert.equal(
      exc.cause,
      "argument --role: invalid choice: 'x' (choose from 'provider', 'deployer').",
    );
  }
});

test("to a command with no positionals, `--` is itself the unrecognized flag, as argparse leaves it", () => {
  const dashes = {
    key: "input.t_unrecognized_flag",
    cause: "unrecognized flag '--'.",
    fix: "FLAG-FIX",
  };
  assert.deepEqual(refusal(["--from", "a", "--"]), dashes);
  assert.deepEqual(refusal(["--from", "a", "--", "-h"]), dashes);
  assert.deepEqual(refusal(["--", "--from", "a"]), dashes);
  // An unknown flag before it is still the first refused.
  assert.equal(refusal(["--nope", "--"]).cause, "unrecognized flag '--nope'.");
});

function mk(control: string, subject: string, outcome: string, deviation?: string): Assertion {
  return assertionFromJson({
    control,
    control_version: "1",
    subject,
    outcome,
    rung: 1,
    mode: "automated",
    window: { start: "2026-01-01T00:00:00Z", end: "2026-01-02T00:00:00Z" },
    population: { applicable: 1, failed: 0 },
    severity: "high",
    family: control.split("-")[0],
    expectations: [],
    violations: [],
    evidence: [],
    ...(deviation !== undefined ? { deviation } : {}),
  });
}

const ASSERTIONS = [
  mk("DAT-01", "spiffe://corp/a", "conformant"),
  mk("DAT-02", "spiffe://corp/a", "non-conformant", "DAT-02"),
  mk("CND-01", "spiffe://x/a_b*[c]<d>&`e`", "insufficient_evidence"),
  mk("ROB-03", "spiffe://corp/a", "conformant", "ROB-03"),
  mk("ROB-04", "spiffe://corp/b", "not_applicable", "ROB-03"),
];

// Python: render_public_statement(assertions, catalogs=["eu-ai-act", "nist_ai*rmf"],
// deviations=applied_deviation_ids(assertions)).
const PY_FULL =
  "# Public conformance statement\n\n## Scope\nSubjects: `spiffe://corp/a`, `spiffe://corp/b`, `spiffe://x/a_b*［c］‹d›＆'e'`\nCatalogs: eu-ai-act, nist_ai*rmf\nDate: (unspecified)\n\n## Outcomes by family\n\n| Family | conformant | non-conformant | partial | not_applicable | not_assessed | insufficient_evidence |\n|---|---|---|---|---|---|---|\n| CND | 0 | 0 | 0 | 0 | 0 | 1 |\n| DAT | 1 | 1 | 0 | 0 | 0 | 0 |\n| ROB | 1 | 0 | 0 | 1 | 0 | 0 |\n\n## Accepted deviations\n`DAT-02`, `ROB-03`\n\n## Conduct\nOver the observation window, the named subjects acted within their declared boundaries and on authorised instructions as evidenced by the Conduct overlay controls listed.\n\n## Affected persons\nAffected persons may obtain an explanation of a decision and raise concerns through the deployer's published contact channel (EU AI Act Arts. 26(11), 85, 86).\n\n## Basis\nThis statement reports conformance to the named catalog as evaluated by the Agent Conformance Engine over the named evidence and observation window. It is not a legal compliance determination.\n";

// Python: render_public_statement(assertions[:2], deviations=applied_deviation_ids(assertions[:2])).
const PY_NO_CATALOGS =
  "# Public conformance statement\n\n## Scope\nSubjects: `spiffe://corp/a`\nCatalogs: (unspecified)\nDate: (unspecified)\n\n## Outcomes by family\n\n| Family | conformant | non-conformant | partial | not_applicable | not_assessed | insufficient_evidence |\n|---|---|---|---|---|---|---|\n| DAT | 1 | 1 | 0 | 0 | 0 | 0 |\n\n## Accepted deviations\n`DAT-02`\n\n## Affected persons\nAffected persons may obtain an explanation of a decision and raise concerns through the deployer's published contact channel (EU AI Act Arts. 26(11), 85, 86).\n\n## Basis\nThis statement reports conformance to the named catalog as evaluated by the Agent Conformance Engine over the named evidence and observation window. It is not a legal compliance determination.\n";

// Python: render_public_statement([], catalogs=[]).
const PY_EMPTY =
  "# Public conformance statement\n\n## Scope\nSubjects: (none)\nCatalogs: (unspecified)\nDate: (unspecified)\n\n## Outcomes by family\n\n| Family | conformant | non-conformant | partial | not_applicable | not_assessed | insufficient_evidence |\n|---|---|---|---|---|---|---|\n\n## Accepted deviations\nNone.\n\n## Affected persons\nAffected persons may obtain an explanation of a decision and raise concerns through the deployer's published contact channel (EU AI Act Arts. 26(11), 85, 86).\n\n## Basis\nThis statement reports conformance to the named catalog as evaluated by the Agent Conformance Engine over the named evidence and observation window. It is not a legal compliance determination.\n";

test("appliedDeviationIds is the sorted, unique deviation ids", () => {
  assert.deepEqual(appliedDeviationIds(ASSERTIONS), ["DAT-02", "ROB-03"]);
  assert.deepEqual(appliedDeviationIds([]), []);
});

test("the public statement matches Python's: deviations, Conduct, catalogs, sanitised subject", () => {
  assert.equal(
    renderPublicStatement(
      ASSERTIONS,
      ["eu-ai-act", "nist_ai*rmf"],
      appliedDeviationIds(ASSERTIONS),
    ),
    PY_FULL,
  );
});

test("the public statement matches Python's with no catalogs and no Conduct family", () => {
  const two = ASSERTIONS.slice(0, 2);
  assert.equal(renderPublicStatement(two, undefined, appliedDeviationIds(two)), PY_NO_CATALOGS);
});

test("the public statement matches Python's with nothing at all", () => {
  assert.equal(renderPublicStatement([], []), PY_EMPTY);
  assert.equal(renderPublicStatement([]), PY_EMPTY);
});
