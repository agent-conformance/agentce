/**
 * Coverage reconciliation is byte-identical to the Python reference (SPEC §6.5, §8.3).
 *
 * `testdata/coverage-{fixture,golden}.json` were emitted by the reference engine's `compute_coverage`
 * over subjects that are covered, below threshold, and uncovered, exercising the `ok`/`gap`/`unknown`
 * roll-ups, repeating-decimal ratio rounding, and a manifest-declared denominator (read from
 * `testdata/coverage-bundle/`).
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { confineToRoot, loadBundle, safeIsFile } from "./bundle";
import { canonicalString } from "./canonical";
import { computeCoverage } from "./coverage";
import { InputError } from "./errors";
import { profileFromDict } from "./profile";

const TESTDATA = join(__dirname, "..", "testdata");

/** The checked bytes loadBundle hands coverage for the listed paths that stay inside `root` (loadBundle refuses a
 * bundle with one that escapes, so its bytes never reach coverage: 18.184f). */
function membersOf(root: string, listed: string[]): Map<string, Buffer> {
  const out = new Map<string, Buffer>();
  for (const rel of listed) {
    const member = confineToRoot(root, rel);
    if (member !== null && safeIsFile(member)) {
      out.set(rel, readFileSync(member));
    }
  }
  return out;
}

test("coverage reconciliation matches the Python reference golden", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "coverage-fixture.json"), "utf-8"));
  const golden = JSON.parse(readFileSync(join(TESTDATA, "coverage-golden.json"), "utf-8"));
  const profile = profileFromDict(fixture.profile);
  const result = computeCoverage(
    fixture.events,
    profile,
    membersOf(join(TESTDATA, "coverage-bundle"), ["reference/prov-counts.json"]),
  );
  assert.equal(canonicalString(result), canonicalString(golden));
});

// --- A denominator manifest is a bundle-adjacent reference: it must stay inside the bundle root
// --- after symlinks resolve, or it is not read at all (SPEC §6.5, §8.1).

const DENOMINATOR_SOURCE = "urn:src:egress";

/** One observed ToolCall plus two of the denominator source's own: the fallback expects 2. */
const EVENTS: Array<Record<string, unknown>> = [
  { source: "urn:src:agent", subject: "subj-A", data: { "@type": "ToolCall" } },
  { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "ToolCall" } },
  { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "ToolCall" } },
];

/** The bundle manifest lists the denominator manifest, so its counts may be read (SPEC §6.5). */
const LISTED = ["reference/counts.json"];

function profileWithManifest(manifest: string) {
  return profileFromDict({
    subjects: [
      {
        id: "subj-A",
        coverage_denominators: [
          { kind: "registry", source: DENOMINATOR_SOURCE, covers: ["ToolCall"], manifest },
        ],
      },
    ],
  });
}

/** The `expected` count the reconciliation used for `subj-A`'s ToolCall events. */
function expectedToolCalls(result: Record<string, unknown>): unknown {
  const subjects = result.subjects as Record<string, { event_types: Record<string, unknown> }>;
  const entry = subjects["subj-A"]?.event_types.ToolCall as { expected: unknown };
  return entry.expected;
}

function withTempBundle(body: (dir: string, root: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-coverage-"));
  try {
    const root = join(dir, "bundle");
    mkdirSync(join(root, "reference"), { recursive: true });
    body(dir, root);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a denominator manifest escaping the bundle root with '..' is not read", () => {
  withTempBundle((dir, root) => {
    writeFileSync(join(dir, "secret.json"), '{"expected": {"ToolCall": 999999}}', "utf-8");

    const result = computeCoverage(
      EVENTS,
      profileWithManifest("../secret.json"),
      membersOf(root, ["../secret.json"]),
    );
    // Never among the checked members (loadBundle refuses the bundle), so nothing is expected from it.
    assert.equal(expectedToolCalls(result), null);
  });
});

test("a denominator manifest whose symlink escapes the bundle root is not read", () => {
  withTempBundle((dir, root) => {
    const outside = join(dir, "secret.json");
    writeFileSync(outside, '{"expected": {"ToolCall": 999999}}', "utf-8");
    symlinkSync(outside, join(root, "reference", "counts.json"));

    const result = computeCoverage(
      EVENTS,
      profileWithManifest("reference/counts.json"),
      membersOf(root, LISTED),
    );
    assert.equal(expectedToolCalls(result), null);
  });
});

test("a denominator manifest inside the bundle root is still read", () => {
  withTempBundle((_dir, root) => {
    writeFileSync(join(root, "reference", "counts.json"), '{"expected": {"ToolCall": 7}}', "utf-8");

    const result = computeCoverage(
      EVENTS,
      profileWithManifest("reference/counts.json"),
      membersOf(root, LISTED),
    );
    assert.equal(expectedToolCalls(result), 7);
  });
});

test("a denominator manifest that is a symlink inside the bundle root is still read", () => {
  withTempBundle((_dir, root) => {
    const real = join(root, "reference", "real-counts.json");
    writeFileSync(real, '{"counts": {"ToolCall": 5}}', "utf-8");
    symlinkSync(real, join(root, "reference", "counts.json"));

    const result = computeCoverage(
      EVENTS,
      profileWithManifest("reference/counts.json"),
      membersOf(root, LISTED),
    );
    assert.equal(expectedToolCalls(result), 5);
  });
});

test("a manifest-reading denominator's own records of a type it counts are never captured calls", () => {
  withTempBundle((_dir, root) => {
    writeFileSync(join(root, "reference", "counts.json"), '{"counts": {"ToolCall": 2}}', "utf-8");
    const events = [
      ...EVENTS,
      { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "SessionStart" } },
    ];

    const result = computeCoverage(
      events,
      profileWithManifest("reference/counts.json"),
      membersOf(root, LISTED),
    );
    const subjects = result.subjects as Record<
      string,
      { event_types: Record<string, { observed: number; expected: unknown; status: string }> }
    >;
    const types = subjects["subj-A"]?.event_types ?? {};
    assert.deepEqual([types.ToolCall?.observed, types.ToolCall?.expected], [1, 2]);
    assert.equal(types.ToolCall?.status, "below_threshold");
    assert.equal(types.SessionStart?.observed, 1);
    assert.equal(types.SessionStart?.status, "unknown");
  });
});

test("a self-counting denominator hides only the types it covers", () => {
  const profile = profileFromDict({
    subjects: [
      {
        id: "subj-A",
        evidence_sources: [{ adapter: "gw", source: "urn:src:gw", class: "enforcement_point" }],
        coverage_denominators: [
          { kind: "egress_proxy_log", source: DENOMINATOR_SOURCE, covers: ["ToolCall"] },
        ],
      },
    ],
  });
  const events = [
    { source: "urn:src:gw", subject: "subj-A", data: { "@type": "ToolCall" } },
    { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "ToolCall" } },
    { source: DENOMINATOR_SOURCE, subject: "subj-A", data: { "@type": "SessionStart" } },
  ];

  const result = computeCoverage(events, profile);
  const subjects = result.subjects as Record<
    string,
    { coverage_status: string; event_types: Record<string, { observed: number; status: string }> }
  >;
  const subject = subjects["subj-A"];
  assert.equal(subject?.event_types.SessionStart?.observed, 1);
  assert.equal(subject?.event_types.SessionStart?.status, "unknown");
  assert.equal(subject?.coverage_status, "unknown");
});

test("coverage denominator rewritten after load counts the checked bytes", () => {
  withTempBundle((dir) => {
    const root = join(dir, "b");
    const checked = '{"counts": {"ToolCall": 1}}';
    mkdirSync(join(root, "reference"), { recursive: true });
    writeFileSync(join(root, "reference", "counts.json"), checked, "utf-8");
    writeFileSync(
      join(root, "manifest.json"),
      JSON.stringify({
        files: [
          {
            path: "reference/counts.json",
            sha256: createHash("sha256").update(checked).digest("hex"),
          },
        ],
      }),
      "utf-8",
    );
    const loaded = loadBundle(root);
    writeFileSync(join(root, "reference", "counts.json"), '{"counts": {"ToolCall": 999}}', "utf-8");
    const profile = profileFromDict({
      subjects: [
        {
          id: "subj-A",
          coverage_denominators: [
            {
              kind: "registry",
              source: "urn:src:egress",
              covers: ["ToolCall"],
              manifest: "reference/counts.json",
            },
          ],
        },
      ],
    });
    const subjects = computeCoverage([], profile, loaded.members).subjects as Record<
      string,
      { event_types: Record<string, { expected: unknown }> }
    >;
    assert.equal(subjects["subj-A"]?.event_types.ToolCall?.expected, 1);
  });
});

// --- 18.184j: every engine refuses the same manifests with one key, never a crash or a truncated count.

/** The coverage of EVENTS when the listed manifest holds `raw` (the checked bytes, as loadBundle hands them). */
function coverageWithManifest(raw: string | Buffer): Record<string, unknown> {
  return computeCoverage(
    EVENTS,
    profileWithManifest("reference/counts.json"),
    new Map([["reference/counts.json", Buffer.from(raw)]]),
  );
}

for (const raw of [
  "not json",
  "",
  Buffer.concat([Buffer.from('{"counts": {"ToolCall": 1}}'), Buffer.from([0xff])]),
  '{"counts": {"ToolCall": NaN}}',
  '{"counts": {"ToolCall": Infinity}}',
  '{"counts": [3]}',
  '{"counts": []}',
  '{"counts": "x"}',
  '{"counts": null}',
  '{"counts": true}',
  '{"counts": {"ToolCall": 1}, "expected": [1]}',
  '{"counts": {"ToolCall": 1}, "expected": {"ToolCall": -1}}',
  '{"counts": {"ToolCall": "x"}}',
  '{"counts": {"ToolCall": "1"}}',
  '{"counts": {"ToolCall": -3}}',
  '{"counts": {"ToolCall": true}}',
  '{"counts": {"ToolCall": null}}',
  '{"counts": {"ToolCall": {"n": 1}}}',
  '{"counts": {"ToolCall": 1.0}}',
  '{"counts": {"ToolCall": 1.5}}',
  '{"counts": {"ToolCall": 1e0}}',
  '{"counts": {"ToolCall": 9007199254740992}}',
]) {
  test(`a denominator manifest holding ${JSON.stringify(raw.toString())} is refused naming the file`, () => {
    assert.throws(
      () => coverageWithManifest(raw),
      (exc: unknown) =>
        exc instanceof InputError &&
        exc.key === "input.coverage_denominator_manifest_invalid" &&
        exc.cause.includes("reference/counts.json"),
    );
  });
}

for (const [raw, expected] of [
  ['{"counts": {"ToolCall": 0}}', 0],
  ['{"counts": {"ToolCall": 3000000000}}', 3000000000],
  ['{"counts": {"ToolCall": 9007199254740991}}', 9007199254740991],
  ['{"counts": {}, "expected": {"ToolCall": 2}}', 2],
  ['{"expected": {"ToolCall": 2}}', 2],
] as const) {
  test(`a denominator manifest holding ${raw} declares ${expected}`, () => {
    assert.equal(expectedToolCalls(coverageWithManifest(raw)), expected);
  });
}

for (const raw of ["[1]", '"counts"', "null", "3", "1.5", "5e3", "9007199254740993"]) {
  test(`a denominator manifest whose root is ${raw} is refused, never read as declaring no counts`, () => {
    // SPEC 6.5: a listed manifest is one JSON object; falling through to the source's own events could raise
    // coverage (18.184r; a number token that reads as a NonCanonicalNumber is not an object either).
    assert.throws(
      () => coverageWithManifest(raw),
      (exc: unknown) =>
        exc instanceof InputError &&
        exc.key === "input.coverage_denominator_manifest_invalid" &&
        exc.cause.includes("reference/counts.json") &&
        exc.cause.includes("is not a JSON object"),
    );
  });
}
