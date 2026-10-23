/**
 * The public conformance statement against Python's `render_public_statement` over the same
 * assertions (18.110). The expected statements are Python's output, pasted verbatim.
 * `ReportCliTest.java` pins the same. report's `choices` and needs-value keys are pinned in
 * `argv.test.ts`.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Assertion, assertionFromJson } from "./assertions";
import { appliedDeviationIds, renderPublicStatement } from "./report";

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
