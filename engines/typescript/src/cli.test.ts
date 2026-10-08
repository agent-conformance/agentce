/**
 * `assess`, `validate`, `report`, and `quickstart` on the real CLI entry point (`main`), over the
 * vendored quickstart project — the same commands `docs/quickstart-typescript.md` tells a Node adopter
 * to run, and the same data every installed package carries (SPEC §13.4 AX-1).
 */

import assert from "node:assert/strict";
import {
  createPublicKey,
  sign as cryptoSign,
  verify as cryptoVerify,
  generateKeyPairSync,
} from "node:crypto";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { quickstartDir } from "./bundled";
import { main } from "./cli";
import { digestBytes, digestTree } from "./report";
import { keyidFor, signStatement } from "./sign";
import { runJson } from "./testSupport";

function runText(argv: string[]): { exitCode: number; lines: string[] } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => {
    lines.push(line);
  };
  let exitCode: number;
  try {
    exitCode = main(argv);
  } finally {
    console.log = original;
  }
  return { exitCode, lines };
}

function diffFixture(dir: string, name: string, records: unknown[]): string {
  const path = join(dir, name);
  writeFileSync(path, JSON.stringify(records));
  return path;
}

const REPO = join(__dirname, "..", "..", "..");
/** A real, vendored catalog (`OVS-03` is `severity: high`) -- the same catalog `catalog.test.ts` uses. */
const CATALOG_DIR = join(REPO, "spec", "catalogs", "base", "eu-ai-act");

function readinessReport(
  dir: string,
  overrides: {
    assertions?: Record<string, unknown>[];
    integrity?: Record<string, unknown>[];
  } = {},
): string {
  const reportDir = join(dir, "report");
  mkdirSync(reportDir);
  writeFileSync(join(reportDir, "assertions.json"), JSON.stringify(overrides.assertions ?? []));
  writeFileSync(
    join(reportDir, "integrity.jsonl"),
    (overrides.integrity ?? []).map((r) => `${JSON.stringify(r)}\n`).join(""),
  );
  writeFileSync(join(reportDir, "coverage.json"), JSON.stringify({ subjects: {} }));
  writeFileSync(join(reportDir, "applicability.jsonl"), "");
  return reportDir;
}

test("quickstart assesses the vendored project end to end and writes a real report", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-quickstart-"));
  try {
    const { exitCode, envelope } = runJson(["quickstart", "--out", out]);
    assert.equal(envelope.quickstart, "ok");
    assert.ok((envelope.assertions as number) > 0, "quickstart evaluated no assertions");
    assert.ok([0, 1].includes(exitCode), `unexpected exit code ${exitCode}`);

    const assertions = JSON.parse(readFileSync(join(out, "assertions.json"), "utf-8"));
    assert.ok(Array.isArray(assertions) && assertions.length > 0, "assertions.json is empty");
    assert.ok(readFileSync(join(out, "report.html"), "utf-8").includes("<html"));
    assert.ok(JSON.parse(readFileSync(join(out, "oscal-ar.json"), "utf-8")));
    assert.ok(JSON.parse(readFileSync(join(out, "results.sarif"), "utf-8")));

    // The manifest's invocation never carries this machine's raw bundle/profile path (SPEC §8.4).
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8"));
    const invocation = (manifest.run as { invocation: string[] }).invocation;
    assert.ok(!invocation.some((part) => part.includes(quickstartDir())), invocation.join(" "));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess on the vendored quickstart bundle matches quickstart's own output", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-assess-"));
  try {
    const quickstart = quickstartDir();
    const { exitCode, envelope } = runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--domain",
      join(quickstart, "domain.linkml.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    assert.ok([0, 1].includes(exitCode), `unexpected exit code ${exitCode}`);
    assert.ok((envelope.assertions as number) > 0);
    assert.equal((envelope.summary as { verdict: string }).verdict, "incomplete");
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess --profile and --domain: manifest.json carries their real sha256 digests through the real CLI (18.42, loophole L18.2)", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-manifest-digest-"));
  try {
    const quickstart = quickstartDir();
    const profilePath = join(quickstart, "applicability.yaml");
    const domainPath = join(quickstart, "domain.linkml.yaml");
    runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      profilePath,
      "--domain",
      domainPath,
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8")) as {
      inputs: Record<string, unknown>;
    };
    assert.equal(
      manifest.inputs.applicability_profile_digest,
      digestBytes(readFileSync(profilePath)),
    );
    assert.equal(manifest.inputs.domain_binding_digest, digestBytes(readFileSync(domainPath)));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess with no --domain: manifest.json has no domain_binding_digest (18.42, loophole L18.2)", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-manifest-no-domain-"));
  try {
    const quickstart = quickstartDir();
    runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8")) as {
      inputs: Record<string, unknown>;
    };
    assert.ok("applicability_profile_digest" in manifest.inputs);
    assert.ok(!("domain_binding_digest" in manifest.inputs));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

/** `verification/gates/fixtures/<name>`: one control (AUD-01, severity: high) the fixture's evidence
 * bundle never satisfies, shared with the Python engine's own 18.30 tests and the VG-AUDIENCE-PRESETS
 * gate (not vendored under `data/`, since it is a cross-engine test fixture, not shipped product data). */
function gateFixtureDir(name: string): string {
  return join(__dirname, "..", "..", "..", "verification", "gates", "fixtures", name);
}

test("assess exits 2 (insufficient_evidence) on a severity: high control (SPEC.md:1076, 18.30)", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-exit2-"));
  try {
    const fixture = gateFixtureDir("audience_presets");
    const { exitCode, envelope } = runJson([
      "assess",
      "--bundle",
      join(fixture, "evidence"),
      "--profile",
      join(fixture, "applicability.yaml"),
      "--domain",
      join(fixture, "domain.linkml.yaml"),
      "--catalog-dir",
      join(fixture, "catalog"),
      "--allow-unverified-catalog",
      "--out",
      out,
    ]);
    assert.equal(exitCode, 2);
    assert.equal(envelope.exit_code, 2);
    assert.deepEqual(envelope.exit_status, ["insufficient_evidence"]);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess exit code 2 is not tripped by a medium-severity insufficient_evidence gap", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-exit2-medium-"));
  try {
    const quickstart = quickstartDir();
    const { exitCode } = runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--domain",
      join(quickstart, "domain.linkml.yaml"),
      "--out",
      out,
    ]);
    assert.notEqual(exitCode, 2);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

/** The quickstart profile with its `catalogs:` list removed, written into `dir`. */
function profileWithoutCatalogs(dir: string): string {
  const kept: string[] = [];
  let inCatalogs = false;
  for (const line of readFileSync(join(quickstartDir(), "applicability.yaml"), "utf-8").split(
    "\n",
  )) {
    if (line === "catalogs:") {
      inCatalogs = true;
    } else if (!(inCatalogs && line.startsWith("  - "))) {
      inCatalogs = false;
      kept.push(line);
    }
  }
  const path = join(dir, "no-catalog.yaml");
  writeFileSync(path, kept.join("\n"));
  return path;
}

function manifestCatalogs(out: string): string[] {
  const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8")) as {
    inputs: { catalogs: Array<{ id: string; version: string }> };
  };
  return manifest.inputs.catalogs.map((c) => `${c.id}@${c.version}`);
}

test("assess with no catalog named evaluates the baseline; an empty --catalog is refused", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-assess-default-"));
  try {
    const quickstart = quickstartDir();
    const argv = [
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      profileWithoutCatalogs(out),
      "--domain",
      join(quickstart, "domain.linkml.yaml"),
    ];
    const run = runJson([...argv, "--out", join(out, "default")]);
    assert.ok([0, 1].includes(run.exitCode), `unexpected exit code ${run.exitCode}`);
    assert.deepEqual(manifestCatalogs(join(out, "default")), ["baseline@2026.09"]);
    const explicit = runJson([...argv, "--catalog", "eu-ai-act@2026.09", "--out", join(out, "eu")]);
    assert.ok([0, 1].includes(explicit.exitCode));
    assert.deepEqual(manifestCatalogs(join(out, "eu")), ["eu-ai-act@2026.09"]);
    const empty = runJson([...argv, "--catalog", ",", "--out", join(out, "none")]);
    assert.equal(empty.exitCode, 3);
    assert.equal(
      (empty.envelope.error as { message_key: string }).message_key,
      "input.catalog_missing",
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("assess refuses an unresolvable catalog with a named input error, not a guess", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-assess-badcat-"));
  try {
    const quickstart = quickstartDir();
    const { exitCode, envelope } = runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--catalog",
      "no-such-catalog@1.0",
      "--out",
      out,
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.catalog_unresolved",
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

const AUDITOR_FIXTURE = join(REPO, "verification", "gates", "fixtures", "auditor_view");
const AUDITOR_REGISTER = join(AUDITOR_FIXTURE, "deviations.yaml");
/** The fixture register's digest, as Python records it (python-reference.md S1). */
const AUDITOR_REGISTER_DIGEST =
  "sha256:ec70a21ea9da1881e0c7737a2c760ef7fcee3aed71e92944499871f8f2f7b439";

function auditorAssessArgs(out: string): string[] {
  return [
    "assess",
    "--json",
    "--out",
    out,
    "--bundle",
    join(AUDITOR_FIXTURE, "evidence"),
    "--profile",
    join(AUDITOR_FIXTURE, "applicability.yaml"),
    "--domain",
    join(AUDITOR_FIXTURE, "domain.linkml.yaml"),
    "--catalog-dir",
    join(AUDITOR_FIXTURE, "catalog"),
    "--allow-unverified-catalog",
  ];
}

/** Run `main` on `argv` as given (no `--json` appended, so a flag can stay last) and parse the envelope. */
function runEnvelope(argv: string[]): { exitCode: number; envelope: Record<string, unknown> } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => {
    lines.push(line);
  };
  let exitCode: number;
  try {
    exitCode = main(argv);
  } finally {
    console.log = original;
  }
  return { exitCode, envelope: JSON.parse(lines.join("\n")) };
}

function deviationFacts(out: string): {
  outcomes: Record<string, [string, string | null]>;
  digest: unknown;
  limitations: string[];
  risks: unknown;
} {
  const assertions = JSON.parse(readFileSync(join(out, "assertions.json"), "utf-8")) as Array<
    Record<string, unknown>
  >;
  const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8"));
  const oscal = JSON.parse(readFileSync(join(out, "oscal-ar.json"), "utf-8"));
  return {
    outcomes: Object.fromEntries(
      assertions.map((a) => [a.control, [a.outcome, (a.deviation as string | undefined) ?? null]]),
    ),
    digest: manifest.inputs.deviation_register_digest,
    limitations: manifest.limitations,
    risks: oscal["assessment-results"].results[0].risks,
  };
}

function withOut(name: string, body: (out: string) => void): void {
  const out = mkdtempSync(join(tmpdir(), `agentce-cli-${name}-`));
  try {
    body(out);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

test("runAssess --deviations applies the register: unexpired flipped, expired reported, digest and risks written", () => {
  withOut("assess-deviations", (out) => {
    const { exitCode } = runEnvelope([...auditorAssessArgs(out), "--deviations", AUDITOR_REGISTER]);
    assert.equal(exitCode, 1); // AUV-02's expired entry leaves it non-conformant
    const facts = deviationFacts(out);
    assert.deepEqual(facts.outcomes["AUV-01"], ["partial", "AUV-01"]);
    assert.deepEqual(facts.outcomes["AUV-02"], ["non-conformant", null]);
    assert.equal(facts.digest, AUDITOR_REGISTER_DIGEST);
    assert.ok(
      facts.limitations.includes(
        "AUV-02: deviation expired 2025-11-01T00:00:00.000Z; ignored and reported, the control remains non-conformant",
      ),
    );
    assert.equal((facts.risks as unknown[]).length, 1);
  });
});

test("runAssess --deviations refuses a register that names a control outside the catalog", () => {
  withOut("assess-deviations-lint", (out) => {
    const register = join(out, "reg.yaml");
    writeFileSync(
      register,
      "deviation_register_version: 1\ndeviations:\n  - control: XYZ-99\n    rationale: r\n" +
        "    compensating_control: c\n    owner: user:a@example.com\n    approver: user:b@example.com\n" +
        '    granted: "2026-01-01T00:00:00Z"\n    expiry: "2026-03-01T00:00:00Z"\n',
    );
    const { exitCode, envelope } = runEnvelope([
      ...auditorAssessArgs(join(out, "report")),
      "--deviations",
      register,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as Record<string, string>;
    assert.equal(error.message_key, "input.deviation_invalid");
    assert.ok(!existsSync(join(out, "report", "assertions.json")));
  });
});

test("assess --deviations=<path> applies exactly like the two-token form", () => {
  withOut("assess-deviations-eq", (out) => {
    const { exitCode } = runEnvelope([
      ...auditorAssessArgs(out),
      `--deviations=${AUDITOR_REGISTER}`,
    ]);
    assert.equal(exitCode, 1);
    const facts = deviationFacts(out);
    assert.deepEqual(facts.outcomes["AUV-01"], ["partial", "AUV-01"]);
    assert.equal(facts.digest, AUDITOR_REGISTER_DIGEST);
  });
});

test("assess bare --deviations is refused with input.assess_flag_needs_value", () => {
  withOut("assess-deviations-bare", (out) => {
    const { exitCode, envelope } = runEnvelope([...auditorAssessArgs(out), "--deviations"]);
    assert.equal(exitCode, 3);
    const error = envelope.error as Record<string, string>;
    assert.equal(error.message_key, "input.assess_flag_needs_value");
    assert.ok(!existsSync(join(out, "assertions.json")));
  });
});

test("assess refuses an unknown or abbreviated flag and a second positional (18.105)", () => {
  for (const extra of [
    ["--em", "md"],
    ["--fo", "ci"],
    ["--emitt", "md"],
    ["--nonsense"],
    ["x", "y"],
  ]) {
    withOut("assess-unknown-flag", (out) => {
      const { exitCode, envelope } = runEnvelope([...auditorAssessArgs(out), ...extra]);
      assert.equal(exitCode, 3, extra.join(" "));
      const error = envelope.error as Record<string, string>;
      assert.equal(error.message_key, "input.assess_unrecognized_flag", extra.join(" "));
      assert.ok(!existsSync(join(out, "assertions.json")));
    });
  }
});

test("quickstart refuses a flag or argument it does not take, and -h prints the usage (18.106)", () => {
  for (const extra of [
    ["--ou", "o"],
    ["--no-such-flag"],
    ["--trust-root", "x"],
    ["extra"],
    ["--json=1"],
    ["--"],
  ]) {
    withOut("quickstart-unknown-flag", (out) => {
      const { exitCode, envelope } = runEnvelope(["quickstart", "--json", "--out", out, ...extra]);
      assert.equal(exitCode, 3, extra.join(" "));
      const error = envelope.error as Record<string, string>;
      assert.equal(error.message_key, "input.quickstart_unrecognized_flag", extra.join(" "));
      assert.deepEqual(readdirSync(out), []);
    });
  }
  withOut("quickstart-help", (out) => {
    const written: string[] = [];
    const original = process.stdout.write;
    process.stdout.write = ((chunk: string) =>
      written.push(chunk) > 0) as typeof process.stdout.write;
    let exitCode: number;
    try {
      exitCode = main(["quickstart", "--out", out, "--no-such-flag", "-h"]);
    } finally {
      process.stdout.write = original;
    }
    assert.equal(exitCode, 0);
    assert.ok(written.join("").startsWith("usage: agentce quickstart [-h]"));
    assert.deepEqual(readdirSync(out), []);
  });
});

test("assess --deviations before another option is refused, never read as the path", () => {
  withOut("assess-deviations-mid", (out) => {
    const args = auditorAssessArgs(out);
    const { exitCode, envelope } = runEnvelope([
      ...args.slice(0, 6),
      "--deviations",
      ...args.slice(6),
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as Record<string, string>).message_key,
      "input.assess_flag_needs_value",
    );
    assert.ok(!existsSync(join(out, "assertions.json")));
  });
});

test("assess repeated --deviations: the last register wins, in either order", () => {
  withOut("assess-deviations-last", (out) => {
    const empty = join(out, "empty.yaml");
    writeFileSync(empty, "deviation_register_version: 1\ndeviations: []\n");
    const emptyDigest = digestBytes(readFileSync(empty));
    for (const [first, last, digest, auv01] of [
      [AUDITOR_REGISTER, empty, emptyDigest, "non-conformant"],
      [empty, AUDITOR_REGISTER, AUDITOR_REGISTER_DIGEST, "partial"],
    ] as const) {
      const report = join(out, "report");
      rmSync(report, { recursive: true, force: true });
      runEnvelope([...auditorAssessArgs(report), "--deviations", first, "--deviations", last]);
      const facts = deviationFacts(report);
      assert.equal(facts.digest, digest);
      assert.equal(facts.outcomes["AUV-01"]?.[0], auv01);
    }
  });
});

/** An assess run's `fail_on` data and exit facts, and whether it wrote assertions.json. */
function failOnRun(argv: string[], out: string) {
  rmSync(out, { recursive: true, force: true });
  const { exitCode, envelope } = runEnvelope(argv);
  return {
    exitCode,
    exitStatus: envelope.exit_status,
    failOn: envelope.fail_on,
    error: envelope.error as Record<string, string> | undefined,
    written: existsSync(join(out, "assertions.json")),
  };
}

test("assess --fail-on gates the exit code", () => {
  withOut("assess-fail-on", (out) => {
    // No flag: any non-conformant assertion fails the run, as before.
    let run = failOnRun(auditorAssessArgs(out), out);
    assert.equal(run.exitCode, 1);
    assert.equal(run.failOn, undefined);
    // A match fails the run; no match passes it even with non-conformant assertions present.
    const hit = 'outcome=="non-conformant" and severity=="high"';
    run = failOnRun([...auditorAssessArgs(out), "--fail-on", hit], out);
    assert.equal(run.exitCode, 1);
    assert.deepEqual(run.exitStatus, ["findings"]);
    assert.deepEqual(run.failOn, { expression: hit, matched: 2 });
    run = failOnRun([...auditorAssessArgs(out), "--fail-on", 'severity=="critical"'], out);
    assert.equal(run.exitCode, 0);
    assert.deepEqual(run.exitStatus, ["ok"]);
    assert.deepEqual(run.failOn, { expression: 'severity=="critical"', matched: 0 });
    // rung compares as its decimal string; the deviation register applies before the match.
    run = failOnRun(
      [...auditorAssessArgs(out), "--fail-on", 'rung=="0" and mode=="manual" and family=="AUV"'],
      out,
    );
    assert.equal((run.failOn as { matched: number }).matched, 1);
    run = failOnRun(
      [
        ...auditorAssessArgs(out),
        "--deviations",
        AUDITOR_REGISTER,
        "--fail-on",
        'control=="AUV-01" and outcome=="non-conformant"',
      ],
      out,
    );
    assert.equal(run.exitCode, 0);
    // A bad expression is refused before anything is written, and before an unknown --catalog or a
    // missing --deviations register.
    for (const extra of [[], ["--catalog", "nope@1"], ["--deviations", join(out, "nope.yaml")]]) {
      run = failOnRun([...auditorAssessArgs(out), ...extra, "--fail-on", "foo"], out);
      assert.equal(run.exitCode, 3);
      assert.equal(run.error?.message_key, "input.fail_on_invalid_expression");
      assert.equal(
        run.error?.detail,
        "--fail-on 'foo' is not a valid expression: unknown field 'foo'; choose from: control, family, mode, outcome, rung, severity, subject",
      );
      assert.equal(run.written, false);
    }
    // A missing bundle is refused first.
    const args = auditorAssessArgs(out);
    args[args.indexOf("--bundle") + 1] = join(out, "nope");
    run = failOnRun([...args, "--fail-on", "foo"], out);
    assert.equal(run.error?.message_key, "input.bundle_not_a_directory");
  });
});

test("assess --fail-on=<expr> and the last --fail-on wins", () => {
  withOut("assess-fail-on-last", (out) => {
    let run = failOnRun([...auditorAssessArgs(out), '--fail-on=severity=="critical"'], out);
    assert.deepEqual(run.failOn, { expression: 'severity=="critical"', matched: 0 });
    run = failOnRun(
      [...auditorAssessArgs(out), "--fail-on", 'control=="AUV-01"', "--fail-on", 'control=="none"'],
      out,
    );
    assert.equal(run.exitCode, 0);
    assert.deepEqual(run.failOn, { expression: 'control=="none"', matched: 0 });
    run = failOnRun(
      [...auditorAssessArgs(out), "--fail-on", 'control=="none"', '--fail-on=control=="AUV-01"'],
      out,
    );
    assert.equal(run.exitCode, 1);
    assert.deepEqual(run.failOn, { expression: 'control=="AUV-01"', matched: 1 });
    // An empty value is a value, refused by the parser, not by the argv scan.
    run = failOnRun([...auditorAssessArgs(out), "--fail-on="], out);
    assert.equal(run.error?.message_key, "input.fail_on_invalid_expression");
    // Values that start with '-' which argparse still reads as values (M4, M7, M8, M9).
    for (const argv of [
      ["--fail-on=-x"],
      ["--fail-on", "-1"],
      ["--fail-on", "-a b"],
      ["--fail-on", "-\u0661"],
    ]) {
      run = failOnRun([...auditorAssessArgs(out), ...argv], out);
      assert.equal(run.error?.message_key, "input.fail_on_invalid_expression", argv.join(" "));
      assert.match(run.error?.detail ?? "", /unexpected character '-' at position 0$/);
    }
  });
});

test("assess --fail-on with no value is refused", () => {
  withOut("assess-fail-on-bare", (out) => {
    const args = auditorAssessArgs(out);
    for (const argv of [
      [...args, "--fail-on"],
      [...args.slice(0, 6), "--fail-on", ...args.slice(6)],
      [...args, "--fail-on", "-x"],
    ]) {
      const run = failOnRun(argv, out);
      assert.equal(run.exitCode, 3);
      assert.deepEqual(run.error, {
        message_key: "input.assess_flag_needs_value",
        detail: "argument --fail-on: expected one argument",
        fix: "pass --fail-on <expression>.",
      });
      assert.equal(run.written, false);
    }
  });
});

test("assess value flags name the first missing value", () => {
  withOut("assess-value-flags", (out) => {
    let run = failOnRun(
      [...auditorAssessArgs(out), "--fail-on", "--deviations", AUDITOR_REGISTER],
      out,
    );
    assert.equal(run.error?.detail, "argument --fail-on: expected one argument");
    run = failOnRun([...auditorAssessArgs(out), "--deviations", "--fail-on", 'control=="x"'], out);
    assert.deepEqual(run.error, {
      message_key: "input.assess_flag_needs_value",
      detail: "argument --deviations: expected one argument",
      fix: "pass --deviations <file>.",
    });
    assert.equal(run.written, false);
  });
});

test("validate quarantines the vendored quickstart bundle's known-bad events", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-validate-"));
  try {
    const { exitCode, envelope } = runJson([
      "validate",
      "--bundle",
      join(quickstartDir(), "evidence"),
      "--out",
      out,
    ]);
    assert.equal(exitCode, 1); // the quickstart bundle carries deliberately quarantined events
    assert.ok((envelope.quarantined as number) > 0);
    const quarantine = readFileSync(join(out, "quarantine.jsonl"), "utf-8").trim().split("\n");
    assert.equal(quarantine.length, envelope.quarantined);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("report re-renders a committed assertions.json to every supported format", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-report-"));
  try {
    const quickstart = quickstartDir();
    runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    const from = join(out, "assertions.json");
    for (const format of ["md", "html", "oscal", "sarif", "pack"]) {
      const { exitCode, envelope } = runJson(["report", "--from", from, "--format", format]);
      assert.equal(exitCode, 0, format);
      assert.ok((envelope.rendering as string).length > 0, format);
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("report --validate refuses a path that is not a directory", () => {
  const { exitCode, envelope } = runJson(["report", "--validate", "/nonexistent"]);
  assert.equal(exitCode, 3);
  assert.equal(
    (envelope.error as { message_key: string }).message_key,
    "input.validate_not_a_directory",
  );
});

test("report --validate accepts a genuine assess run and rejects a corrupted one", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-report-validate-"));
  try {
    const quickstart = quickstartDir();
    runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    const clean = runJson(["report", "--validate", out]);
    assert.equal(clean.exitCode, 0, JSON.stringify(clean.envelope.problems));
    assert.equal(clean.envelope.valid, true);
    assert.deepEqual(clean.envelope.problems, []);

    const assertionsPath = join(out, "assertions.json");
    const assertions = JSON.parse(readFileSync(assertionsPath, "utf-8"));
    assertions[0].control = undefined;
    writeFileSync(assertionsPath, JSON.stringify(assertions));
    const corrupted = runJson(["report", "--validate", out]);
    assert.equal(corrupted.exitCode, 3);
    assert.equal(corrupted.envelope.valid, false);
    assert.ok(
      (corrupted.envelope.problems as string[]).some((p) => p.includes("assertions.json")),
      JSON.stringify(corrupted.envelope.problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("report --validate catches a real-schema-only OSCAL violation past the local profile", () => {
  const out = mkdtempSync(join(tmpdir(), "agentce-cli-report-validate-oscal-"));
  try {
    const quickstart = quickstartDir();
    runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      "--out",
      out,
    ]);
    const oscalPath = join(out, "oscal-ar.json");
    const oscal = JSON.parse(readFileSync(oscalPath, "utf-8"));
    oscal["assessment-results"].uuid = "not-a-uuid"; // AgentCE's local profile types uuid as a bare
    // string (no pattern); only the real vendored NIST 1.1.2 schema enforces the UUID pattern.
    writeFileSync(oscalPath, JSON.stringify(oscal));
    const { exitCode, envelope } = runJson(["report", "--validate", out]);
    assert.equal(exitCode, 3);
    assert.ok(
      (envelope.problems as string[]).some((p) =>
        p.startsWith("oscal-ar.json (NIST OSCAL 1.1.2): "),
      ),
      JSON.stringify(envelope.problems),
    );
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("version --json returns the same structured envelope every other command produces", () => {
  const { exitCode, envelope } = runJson(["version"]);
  assert.equal(envelope.command, "version");
  assert.equal(envelope.engine, "agentce-ts");
  assert.equal(typeof envelope.engine_version, "string");
  assert.equal(envelope.spec_version, "0.6");
  assert.deepEqual(envelope.supported_catalogs, []);
  assert.equal(envelope.no_ml, "pass");
  assert.deepEqual(envelope.no_ml_detail, { result: "pass", denylisted_present: [] });
  assert.ok(Array.isArray(envelope.exit_codes));
  assert.equal(exitCode, 0);
});

test("plain `version` prints exactly two human-readable lines, distinct from --version/-V", () => {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main(["version"]);
  } finally {
    console.log = original;
  }
  assert.equal(exitCode, 0);
  assert.equal(lines.length, 2);
  assert.match(lines[0] as string, /^agentce-ts \S+ \(spec 0\.6\)$/);
  assert.equal(lines[1], "no_ml: pass");

  const flagLines: string[] = [];
  console.log = (line: string) => flagLines.push(line);
  try {
    main(["--version"]);
    main(["-V"]);
  } finally {
    console.log = original;
  }
  assert.equal(flagLines.length, 2);
  assert.match(flagLines[0] as string, /^agentce \S+$/);
  assert.equal(flagLines[0], flagLines[1]);
});

test("digest-tree prints one catalog directory's real content digest", () => {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main([
      "digest-tree",
      join(__dirname, "..", "..", "..", "spec", "model", "test-vectors", "digest-tree"),
    ]);
  } finally {
    console.log = original;
  }
  assert.equal(exitCode, 0);
  const expected = readFileSync(
    join(__dirname, "..", "..", "..", "spec", "model", "test-vectors", "digest-tree.expected"),
    "utf-8",
  ).trim();
  assert.equal(lines[0], expected);
});

test("security-view CLI verb runs the fixture and prints a standards_citations array", () => {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => lines.push(line);
  let exitCode: number;
  try {
    exitCode = main([
      "security-view",
      join(
        __dirname,
        "..",
        "..",
        "..",
        "verification",
        "gates",
        "fixtures",
        "security_view",
        "activity_and_assertions.json",
      ),
    ]);
  } finally {
    console.log = original;
  }
  assert.equal(exitCode, 0);
  const securityView = JSON.parse(lines[0] as string);
  const citations = securityView.standards_citations as { framework: string }[];
  assert.ok(Array.isArray(citations));
  assert.deepEqual(
    new Set(citations.map((c) => c.framework)),
    new Set(["owasp-asi-2026", "mitre-atlas", "owasp-acs"]),
  );
});

test("diff --json: envelope carries report_a/report_b/changed/diff/what_changed; exit 1 on a real change", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", [
      { control: "C-01", subject: "s1", outcome: "non-conformant" },
    ]);
    const b = diffFixture(dir, "b.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, envelope } = runJson(["diff", a, b]);
    assert.equal(exitCode, 1);
    assert.equal(envelope.report_a, a);
    assert.equal(envelope.report_b, b);
    assert.equal(envelope.changed, 1);
    assert.deepEqual(envelope.diff, [
      { control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" },
    ]);
    assert.deepEqual(envelope.what_changed, {
      closed: [{ control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" }],
      opened: [],
      other: [],
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff identical inputs: exit 0, text 'no differences' (no trailing period)", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, lines } = runText(["diff", a, a]);
    assert.equal(exitCode, 0);
    assert.deepEqual(lines, ["no differences"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff --format md, identical inputs: the fixed three-line section, 'no differences.' with a period", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, lines } = runText(["diff", a, a, "--format", "md"]);
    assert.equal(exitCode, 0);
    assert.deepEqual(lines, ["## What changed", "", "no differences."]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff --format json (without --json): one note line, byte-equal to the sorted-keys envelope data", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", [
      { control: "C-01", subject: "s1", outcome: "conformant" },
    ]);
    const { exitCode, lines } = runText(["diff", a, b, "--format", "json"]);
    assert.equal(exitCode, 1);
    assert.equal(lines.length, 1);
    const parsed = JSON.parse(lines[0] as string);
    assert.equal(parsed.changed, 1);
    assert.equal(parsed.report_a, a);
    assert.ok(
      !("command" in parsed),
      "note-rendered JSON is result.data only, never the full envelope",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: missing report_b gives input.report_b_missing with the diff-specific fix text, exit 3", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const { exitCode, envelope } = runJson(["diff", a]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; fix: string };
    assert.equal(error.message_key, "input.report_b_missing");
    assert.equal(error.fix, "pass two assertion files: `agentce diff <report-a> <report-b>`.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: a malformed (not-JSON) input file gives a keyed internal.unexpected result, never a crash", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = join(dir, "a.json");
    writeFileSync(a, "not json");
    const b = diffFixture(dir, "b.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; fix: string };
    assert.equal(error.message_key, "internal.unexpected");
    assert.equal(
      error.fix,
      "re-run with --debug to see the stack trace, then file an issue for an AgentCE maintainer to investigate.",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: --debug on a malformed input file re-throws instead of returning a keyed envelope", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = join(dir, "a.json");
    writeFileSync(a, "not json");
    const b = diffFixture(dir, "b.json", []);
    assert.throws(() => main(["diff", a, b, "--debug"]));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: an extra positional argument gives input.diff_extra_argument", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", []);
    const c = diffFixture(dir, "c.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b, c]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.diff_extra_argument",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: an unrecognized flag gives input.diff_unrecognized_flag, never a silent positional read", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b, "--forma", "text"]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.diff_unrecognized_flag",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: --debug and --quiet are accepted and silently ignored, matching every other command", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const { exitCode, lines } = runText(["diff", a, a, "--debug", "--quiet"]);
    assert.equal(exitCode, 0);
    assert.deepEqual(lines, ["no differences"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: --format outside {text, json, md} gives input.diff_format with the exact Python fix text", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", []);
    const { exitCode, envelope } = runJson(["diff", a, b, "--format", "yaml"]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; fix: string };
    assert.equal(error.message_key, "input.diff_format");
    assert.equal(error.fix, "pass --format text|json|md.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff: the echoed report_a/report_b path keeps '..' unchanged (normalizePosixPath, not path.normalize)", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    diffFixture(dir, "a.json", []);
    mkdirSync(join(dir, "sub"));
    const raw = `${dir}/sub/../a.json`; // a real, existing file via '..'; the literal segment must survive the echo
    const b = diffFixture(dir, "b.json", []);
    const { envelope } = runJson(["diff", raw, b]);
    assert.ok((envelope.report_a as string).includes("sub/../a.json"), envelope.report_a as string);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("diff --json escapes non-ASCII content exactly like Python's ensure_ascii=True", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-diff-"));
  try {
    const a = diffFixture(dir, "a.json", []);
    const b = diffFixture(dir, "b.json", [{ control: "cé", subject: "😀", outcome: "conformant" }]);
    const lines: string[] = [];
    const original = console.log;
    console.log = (line: string) => lines.push(line);
    try {
      main(["diff", a, b, "--json"]);
    } finally {
      console.log = original;
    }
    const raw = lines.join("\n");
    assert.ok(raw.includes("c\\u00e9"), raw);
    assert.ok(raw.includes("\\ud83d\\ude00"), raw);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- readiness (item 18.25) ---------------------------------------------------------------------

test("readiness: a clean report is READY, exit 0, and writes a real report-readiness-*.md file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, {
      assertions: [{ control: "OVS-03", outcome: "conformant", subject: "s" }],
      integrity: [{ status: "verified", stream: "a" }],
    });
    const { exitCode, envelope } = runJson(["readiness", report, "--catalog-dir", CATALOG_DIR]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.verdict, "READY");
    assert.deepEqual(envelope.reasons, []);
    const reportPath = envelope.report as string;
    assert.ok(reportPath.includes("report-readiness-"), reportPath);
    const written = readFileSync(reportPath, "utf-8");
    assert.ok(written.startsWith("# Report readiness — READY\n"), written);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: broken integrity is NOT READY, exit 1, blocking reasons section written", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, { integrity: [{ status: "failed", stream: "gw" }] });
    const { exitCode, envelope } = runJson(["readiness", report, "--catalog-dir", CATALOG_DIR]);
    assert.equal(exitCode, 1);
    assert.equal(envelope.verdict, "NOT READY");
    const reportPath = envelope.report as string;
    const written = readFileSync(reportPath, "utf-8");
    assert.ok(written.includes("## Blocking reasons"), written);
    assert.ok(written.includes("- integrity failed on stream gw"), written);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a high-severity insufficient_evidence recorded in --gaps is READY WITH LIMITATIONS", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, {
      assertions: [{ control: "OVS-03", outcome: "insufficient_evidence", subject: "s" }],
    });
    const gaps = join(dir, "gaps.md");
    writeFileSync(gaps, "OVS-03 owned by alice on 2026-02-01\n");
    const deviations = join(dir, "deviations.yaml");
    writeFileSync(deviations, "deviations: []\n");
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--gaps",
      gaps,
      "--deviations",
      deviations,
    ]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.verdict, "READY WITH LIMITATIONS");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: missing report_dir gives input.report_dir_missing with the readiness-specific fix text", () => {
  const { exitCode, envelope } = runJson(["readiness"]);
  assert.equal(exitCode, 3);
  const error = envelope.error as { message_key: string; fix: string };
  assert.equal(error.message_key, "input.report_dir_missing");
  assert.equal(error.fix, "pass the report directory: `agentce readiness <report-dir>`.");
});

test("readiness: a report_dir that is not a directory gives input.report_dir_not_a_directory", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const notADir = join(dir, "nope");
    const { exitCode, envelope } = runJson(["readiness", notADir]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.report_dir_not_a_directory",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: an unrecognized flag gives input.readiness_unrecognized_flag, never a silent misread of report_dir", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson(["readiness", "--gasp", report]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.readiness_unrecognized_flag",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a given-but-bad --gaps path is input.gaps_not_a_file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--gaps",
      join(dir, "missing.md"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "input.gaps_not_a_file");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a given-but-bad --deviations path is input.deviations_not_a_file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--deviations",
      join(dir, "missing.yaml"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.deviations_not_a_file",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a bad --catalog-dir is input.catalog-dir_not_a_directory", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      join(dir, "no-such-catalog"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.catalog-dir_not_a_directory",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: a malformed deviation register gives input.deviation_invalid, never a crash", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir);
    const deviations = join(dir, "deviations.yaml");
    writeFileSync(deviations, "deviations: not-a-list\n");
    const { exitCode, envelope } = runJson([
      "readiness",
      report,
      "--catalog-dir",
      CATALOG_DIR,
      "--deviations",
      deviations,
    ]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.deviation_invalid",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("readiness: with no --catalog-dir, every vendored base catalog is used", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-readiness-"));
  try {
    const report = readinessReport(dir, {
      assertions: [{ control: "OVS-03", outcome: "conformant", subject: "s" }],
    });
    const { exitCode, envelope } = runJson(["readiness", report]);
    assert.ok([0, 1].includes(exitCode));
    assert.ok(typeof envelope.verdict === "string");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --- sign (item 18.26) ---------------------------------------------------------------------------

/** A fresh Ed25519 PEM key file, written under `dir`, and the raw 32-byte public key it derives. */
function edKeyFile(dir: string, name = "key.pem"): { path: string; rawPublicKey: Buffer } {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const path = join(dir, name);
  writeFileSync(path, privateKey.export({ type: "pkcs8", format: "pem" }));
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  return { path, rawPublicKey: Buffer.from(publicDer.subarray(12)) };
}

/** A READY report directory (reusing {@link readinessReport}'s clean shape) with a `claim.json` to
 * sign, the minimal shape `_sign_subjects`/`cmd_sign` need. */
function signReportDir(
  dir: string,
  claim: Record<string, unknown> = { claimant: { org: "acme" } },
): string {
  const reportDir = readinessReport(dir);
  writeFileSync(join(reportDir, "claim.json"), JSON.stringify(claim));
  return reportDir;
}

function edPublicKeyObject(rawPublicKey: Buffer) {
  return createPublicKey({
    key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), rawPublicKey]),
    format: "der",
    type: "spki",
  });
}

test("sign: kms profile happy path signs claim.json, writes a detached signature, exit 0", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { path: keyPath, rawPublicKey } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.as, "claimant");
    assert.equal(envelope.profile, "kms");
    assert.equal(envelope.dry_run, false);
    assert.equal(envelope.readiness, "READY");
    assert.equal(envelope.signatures, 1);
    const detachedPath = envelope.signature as string;
    assert.ok(existsSync(detachedPath));
    const detached = JSON.parse(readFileSync(detachedPath, "utf-8"));
    assert.equal(detached.role, "claimant");
    assert.equal(detached.profile, "kms");
    assert.equal(detached.payloadType, "application/vnd.in-toto+json");
    assert.equal(detached.signatures[0].keyid, envelope.keyid);

    const claim = JSON.parse(readFileSync(join(report, "claim.json"), "utf-8"));
    assert.equal(claim.signatures.length, 1);
    assert.deepEqual(claim.signatures[0], detached);
    assert.ok(!("trust_root" in envelope));
    assert.ok(!existsSync(join(report, "trust-root.json")));

    // The signature actually verifies against the key's real derived public key -- a real
    // cryptographic round trip, not a shape-only assertion.
    const payload = Buffer.from(detached.payload as string, "base64");
    const statement = JSON.parse(payload.toString("utf-8"));
    assert.equal(statement._type, "https://in-toto.io/Statement/v1");
    assert.equal(statement.predicate.role, "claimant");
    assert.equal(statement.predicate.profile, "kms");
    const pae = Buffer.concat([
      Buffer.from("DSSEv1 28 application/vnd.in-toto+json ", "ascii"),
      Buffer.from(String(payload.length), "ascii"),
      Buffer.from(" ", "ascii"),
      payload,
    ]);
    const sig = Buffer.from(detached.signatures[0].sig as string, "base64");
    assert.equal(cryptoVerify(null, pae, edPublicKeyObject(rawPublicKey), sig), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --write-trust-root writes a trust-root.json whose public key verifies the signature", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir, { claimant: { org: "acme corp" } });
    const { path: keyPath, rawPublicKey } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "assessor",
      "--profile",
      "kms",
      "--key",
      keyPath,
      "--write-trust-root",
    ]);
    assert.equal(exitCode, 0);
    const trustRootPath = envelope.trust_root as string;
    assert.ok(existsSync(trustRootPath));
    const trustRoot = JSON.parse(readFileSync(trustRootPath, "utf-8"));
    const keyid = envelope.keyid as string;
    assert.equal(trustRoot.keys[keyid].public_key, rawPublicKey.toString("base64"));
    assert.equal(trustRoot.keys[keyid].identity, "acme corp");

    const detached = JSON.parse(readFileSync(envelope.signature as string, "utf-8"));
    const payload = Buffer.from(detached.payload as string, "base64");
    const pae = Buffer.concat([
      Buffer.from("DSSEv1 28 application/vnd.in-toto+json ", "ascii"),
      Buffer.from(String(payload.length), "ascii"),
      Buffer.from(" ", "ascii"),
      payload,
    ]);
    const sig = Buffer.from(detached.signatures[0].sig as string, "base64");
    const publicKeyObject = createPublicKey({
      key: Buffer.concat([
        Buffer.from("302a300506032b6570032100", "hex"),
        Buffer.from(trustRoot.keys[keyid].public_key, "base64"),
      ]),
      format: "der",
      type: "spki",
    });
    assert.equal(cryptoVerify(null, pae, publicKeyObject, sig), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --write-trust-root defaults identity to 'unset' when claimant.org is absent", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir, {});
    const { path: keyPath } = edKeyFile(dir);
    const { envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
      "--write-trust-root",
    ]);
    const trustRoot = JSON.parse(readFileSync(envelope.trust_root as string, "utf-8"));
    const keyid = envelope.keyid as string;
    assert.equal(trustRoot.keys[keyid].identity, "unset");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --dry-run writes nothing, touches no key, even with a bad --key value", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const claimPath = join(report, "claim.json");
    const before = readFileSync(claimPath, "utf-8");
    const beforeMtime = statSync(claimPath).mtimeMs;
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      join(dir, "does-not-exist.pem"),
      "--dry-run",
    ]);
    assert.equal(exitCode, 0);
    assert.equal(envelope.dry_run, true);
    assert.equal(envelope.readiness, "READY");
    assert.ok(!("signature" in envelope));
    assert.equal(readFileSync(claimPath, "utf-8"), before);
    assert.equal(statSync(claimPath).mtimeMs, beforeMtime);
    assert.ok(!existsSync(join(report, "signatures")));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a second sign call appends a second signatures[] entry rather than replacing the first", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const key1 = edKeyFile(dir, "key1.pem");
    const key2 = edKeyFile(dir, "key2.pem");
    runJson(["sign", report, "--as", "claimant", "--profile", "kms", "--key", key1.path]);
    const { envelope } = runJson([
      "sign",
      report,
      "--as",
      "assessor",
      "--profile",
      "kms",
      "--key",
      key2.path,
    ]);
    assert.equal(envelope.signatures, 2);
    const claim = JSON.parse(readFileSync(join(report, "claim.json"), "utf-8"));
    assert.equal(claim.signatures.length, 2);
    assert.equal(claim.signatures[0].role, "claimant");
    assert.equal(claim.signatures[1].role, "assessor");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a NOT_READY report refuses with sign.not_ready before touching any key or file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = readinessReport(dir, { integrity: [{ status: "failed", stream: "gw" }] });
    writeFileSync(join(report, "claim.json"), JSON.stringify({}));
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.not_ready");
    assert.ok(error.detail.startsWith("the report is NOT READY: "));
    assert.equal(
      error.fix,
      "resolve the blocking reasons (agentce readiness <report-dir>) before signing.",
    );
    assert.equal(
      JSON.parse(readFileSync(join(report, "claim.json"), "utf-8")).signatures,
      undefined,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --as absent gives input.sign_role", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "input.sign_role");
    assert.equal(error.detail, "--as must be `claimant` or `assessor`.");
    assert.equal(error.fix, "pass --as claimant|assessor.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --as bogus gives input.sign_role", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "bogus"]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "input.sign_role");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an unknown --profile gives input.sign_profile with a pyRepr-quoted profile name", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "bogus",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "input.sign_profile");
    assert.equal(error.detail, "unknown signing profile 'bogus'.");
    assert.equal(error.fix, "choose one of: sigstore-public, sigstore-private, kms.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --profile \"it's\" gives input.sign_profile with repr's double-quote switch", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "it's",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string };
    assert.equal(error.message_key, "input.sign_profile");
    assert.equal(error.detail, `unknown signing profile "it's".`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --profile '' falls through to the sigstore-public default, then refuses keyless_offline", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "claimant", "--profile", ""]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string };
    assert.equal(error.message_key, "sign.keyless_offline");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an omitted --profile defaults to sigstore-public, which refuses offline", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "claimant"]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.keyless_offline");
    assert.equal(
      error.detail,
      "the sigstore-public profile is keyless and obtains a certificate from a Fulcio instance " +
        "(network); the engine does not sign it offline.",
    );
    assert.equal(
      error.fix,
      "use --profile kms --key <file> offline, or run keyless signing where the Fulcio and Rekor " +
        "endpoints are reachable.",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --profile sigstore-private also refuses offline with sign.keyless_offline, even with --key given", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "sigstore-private",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "sign.keyless_offline");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --write-trust-root without profile kms gives sign.trust_root_requires_kms, quoting the profile", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--write-trust-root",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.trust_root_requires_kms");
    assert.equal(
      error.detail,
      "--write-trust-root needs an exportable public key; the 'sigstore-public' profile has none.",
    );
    assert.equal(
      error.fix,
      "pass --profile kms --key <ed25519-private-key.pem> --write-trust-root.",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a missing claim.json gives sign.no_claim", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = readinessReport(dir);
    const { path: keyPath } = edKeyFile(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.no_claim");
    assert.equal(error.detail, "the report directory has no claim.json to sign.");
    assert.equal(error.fix, "produce the report first: `agentce assess … --out <report-dir>`.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

for (const [raw, cause] of [
  [Buffer.from('{"a": "\xff"}', "latin1"), "claim.json is not valid JSON."],
  [Buffer.from('{"a": '), "claim.json is not valid JSON."],
  [Buffer.from("{} GARBAGE"), "claim.json is not valid JSON."],
  [Buffer.from('{"a": "\\ud800"}'), "claim.json is not valid JSON."],
  [Buffer.from("[1, 2]"), "claim.json is not an object."],
  [Buffer.from('{"signatures": null}'), "claim.json's signatures field is not a list."],
  [Buffer.from('{"signatures": "x"}'), "claim.json's signatures field is not a list."],
  [Buffer.from('{"signatures": {}}'), "claim.json's signatures field is not a list."],
] as const) {
  test(`sign: claim.json ${JSON.stringify(raw.toString("latin1"))} gives sign.claim_malformed before the key or a dry run`, () => {
    const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
    try {
      const report = readinessReport(dir);
      const claimPath = join(report, "claim.json");
      writeFileSync(claimPath, raw);
      for (const extra of [["--key", join(dir, "missing.pem")], ["--dry-run"]]) {
        const { exitCode, envelope } = runJson([
          "sign",
          report,
          "--as",
          "claimant",
          "--profile",
          "kms",
          ...extra,
        ]);
        assert.equal(exitCode, 3);
        const error = envelope.error as { message_key: string; detail: string };
        assert.equal(error.message_key, "sign.claim_malformed");
        assert.equal(error.detail, cause);
      }
      assert.deepEqual(readFileSync(claimPath), raw);
      assert.equal(existsSync(join(report, "signatures")), false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

test("sign: a dry run with no claim.json gives sign.no_claim", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = readinessReport(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--as", "claimant", "--dry-run"]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "sign.no_claim");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: profile kms with no --key gives sign.kms_key_missing", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.kms_key_missing");
    assert.equal(error.detail, "the kms profile signs with an operator-held key.");
    assert.equal(error.fix, "pass --key <ed25519-private-key.pem>.");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: profile kms with --key '' gives sign.kms_key_missing (falsy, like an omitted --key)", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      "",
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "sign.kms_key_missing");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: --key given but not an existing file gives input.key_not_a_file", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      join(dir, "no-such-key.pem"),
    ]);
    assert.equal(exitCode, 3);
    assert.equal((envelope.error as { message_key: string }).message_key, "input.key_not_a_file");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: a non-Ed25519 key gives sign.key_algorithm with the exact catalogued text", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const keyPath = join(dir, "rsa.pem");
    writeFileSync(keyPath, privateKey.export({ type: "pkcs8", format: "pem" }));
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.key_algorithm");
    assert.equal(error.detail, "the signing key is not an Ed25519 private key.");
    assert.equal(
      error.fix,
      "supply an Ed25519 key (the algorithm the engine signs with, SPEC §8.7).",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an unparseable key file gives sign.key_unreadable with the exact catalogued text", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const keyPath = join(dir, "garbage.pem");
    writeFileSync(keyPath, "not a pem\n");
    const { exitCode, envelope } = runJson([
      "sign",
      report,
      "--as",
      "claimant",
      "--profile",
      "kms",
      "--key",
      keyPath,
    ]);
    assert.equal(exitCode, 3);
    const error = envelope.error as { message_key: string; detail: string; fix: string };
    assert.equal(error.message_key, "sign.key_unreadable");
    assert.equal(
      error.detail,
      "the signing key file could not be parsed as an unencrypted PEM private key.",
    );
    assert.equal(
      error.fix,
      "supply an unencrypted Ed25519 private key PEM (`openssl genpkey -algorithm ed25519 " +
        "-out key.pem`, or `agentce catalog sign --new-key <path>`).",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("sign: an unrecognized flag gives input.sign_unrecognized_flag, never a silent misread of report_dir", () => {
  const dir = mkdtempSync(join(tmpdir(), "agentce-cli-sign-"));
  try {
    const report = signReportDir(dir);
    const { exitCode, envelope } = runJson(["sign", report, "--role", "claimant"]);
    assert.equal(exitCode, 3);
    assert.equal(
      (envelope.error as { message_key: string }).message_key,
      "input.sign_unrecognized_flag",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verify: a usage error is the keyed envelope Python and Java give, and an abbreviated flag is refused", () => {
  const cases: [string[], string][] = [
    [["--bogus", "x"], "unrecognized flag '--bogus'."],
    [["--cat", "x"], "unrecognized flag '--cat'."],
    [["--catalog"], "flag '--catalog' needs a value."],
    [["--catalog", "x", "extra"], "unrecognized argument 'extra'."],
    [["--", "--catalog", "x"], "unrecognized flag '--'."],
    [["--catalog", "x", "--signer-trust-root"], "flag '--signer-trust-root' needs a value."],
    [["--json=1", "--catalog", "x"], "flag '--json' takes no value."],
  ];
  for (const [argv, detail] of cases) {
    const { exitCode, envelope } = runJson(["verify", ...argv]);
    const error = envelope.error as { message_key: string; detail: string };
    assert.equal(exitCode, 3, argv.join(" "));
    assert.deepEqual([error.message_key, error.detail], ["input.verify_unrecognized_flag", detail]);
  }
});

// --- assess --catalog-dir signature verification (SPEC §8.7, 18.36): every scenario in the Python
// reference capture (evidence P18-18.36/python-reference.md), with Python's literal cause/fix text. ---

const UNSIGNED_REASON =
  "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).";
const TRUST_ROOT_FIX =
  "pass --trust-root <file> (or set AGENTCE_TRUST_ROOT) to a trust root in the form of the " +
  "engine's vendored data/trust/dev-root.json.";
const UNVERIFIED_FIX =
  "point --catalog-dir at a catalog whose catalog.sig.json verifies, or pass --trust-root <file> " +
  "(or set AGENTCE_TRUST_ROOT) for the root that signed it; --allow-unverified-catalog assesses it " +
  "anyway and records the override as a limitation.";

interface CatalogKey {
  readonly keyid: string;
  /** A `--trust-root` file that trusts this key and nothing else. */
  readonly trustRoot: string;
  /** Signs `dir` over its current bytes (the `catalog.sig.json` shape `catalog sign` writes). */
  signDir(dir: string): void;
}

/** A throwaway Ed25519 catalog-signing key under `work`, as `test_catalog_signature.py` builds. */
function catalogKey(work: string, name: string): CatalogKey {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = Buffer.from(publicKey.export({ type: "spki", format: "der" }).subarray(12));
  const keyid = keyidFor(raw);
  const trustRoot = join(work, `${name}-trust-root.json`);
  writeFileSync(
    trustRoot,
    JSON.stringify({
      keys: { [keyid]: { public_key: raw.toString("base64"), identity: "test://trusted-signer" } },
    }),
  );
  return {
    keyid,
    trustRoot,
    signDir(dir: string): void {
      rmSync(join(dir, "catalog.sig.json"), { force: true });
      const digest = digestTree(dir, new Set(["catalog.sig.json"]));
      const statement = {
        _type: "https://in-toto.io/Statement/v1",
        subject: [{ name: "catalog", digest: { sha256: digest.slice("sha256:".length) } }],
        predicateType: "https://agent-conformance.org/attestation/catalog/v1",
        predicate: {},
      };
      const envelope = signStatement(statement, {
        keyid,
        sign: (data: Buffer) => cryptoSign(null, data, privateKey),
      });
      writeFileSync(join(dir, "catalog.sig.json"), JSON.stringify(envelope));
    },
  };
}

/** A copy of the base EU AI Act catalog at `work/<name>`, with its own signature removed. */
function unsignedCatalogCopy(work: string, name: string): string {
  const dir = join(work, name);
  cpSync(join(REPO, "spec", "catalogs", "base", "eu-ai-act"), dir, { recursive: true });
  rmSync(join(dir, "catalog.sig.json"));
  return dir;
}

function byoAssess(work: string, extra: string[]): ReturnType<typeof runJson> & { out: string } {
  const quickstart = quickstartDir();
  const out = join(work, "out");
  return {
    ...runJson([
      "assess",
      "--bundle",
      join(quickstart, "evidence"),
      "--profile",
      join(quickstart, "applicability.yaml"),
      "--domain",
      join(quickstart, "domain.linkml.yaml"),
      "--catalog",
      "eu-ai-act@2026.09",
      ...extra,
      "--out",
      out,
    ]),
    out,
  };
}

function refusal(envelope: Record<string, unknown>): { key: string; cause: string; fix: string } {
  const error = envelope.error as { message_key: string; detail: string; fix: string };
  return { key: error.message_key, cause: error.detail, fix: error.fix };
}

function withWork(prefix: string, body: (work: string) => void): void {
  const work = mkdtempSync(join(tmpdir(), prefix));
  try {
    body(work);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

test("assess refuses a --catalog-dir signed by a key the trust root does not know (18.36 §1)", () => {
  withWork("agentce-byo-untrusted-", (work) => {
    const key = catalogKey(work, "stranger");
    const dir = unsignedCatalogCopy(work, "untrusted-cat");
    key.signDir(dir);
    const { exitCode, envelope, out } = byoAssess(work, ["--catalog-dir", dir]);
    assert.equal(exitCode, 3);
    assert.deepEqual(refusal(envelope), {
      key: "input.catalog_unverified",
      cause: `the catalog directory untrusted-cat did not verify against the effective trust root: no signature verified against the trust root: no trusted key for keyid '${key.keyid}'`,
      fix: UNVERIFIED_FIX,
    });
    assert.equal(existsSync(out), false, "a refused run wrote output");
  });
});

test("assess refuses an unsigned --catalog-dir without the override (18.36 §2)", () => {
  withWork("agentce-byo-unsigned-", (work) => {
    const dir = unsignedCatalogCopy(work, "unsigned-cat");
    const { exitCode, envelope } = byoAssess(work, ["--catalog-dir", dir]);
    assert.equal(exitCode, 3);
    assert.deepEqual(refusal(envelope), {
      key: "input.catalog_unverified",
      cause: `the catalog directory unsigned-cat did not verify against the effective trust root: ${UNSIGNED_REASON}`,
      fix: UNVERIFIED_FIX,
    });
  });
});

test("--allow-unverified-catalog assesses an unsigned catalog and records the limitation (18.36 §3)", () => {
  withWork("agentce-byo-override-", (work) => {
    const dir = unsignedCatalogCopy(work, "unsigned-cat");
    const { exitCode, envelope, out } = byoAssess(work, [
      "--catalog-dir",
      dir,
      "--allow-unverified-catalog",
    ]);
    assert.equal(exitCode, 0);
    const expected = [
      `catalog eu-ai-act@2026.09 at unsigned-cat was used unverified (--allow-unverified-catalog): ${UNSIGNED_REASON}`,
    ];
    assert.deepEqual(envelope.limitations, expected);
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8"));
    assert.deepEqual(manifest.limitations, expected);
    const assertions = JSON.parse(readFileSync(join(out, "assertions.json"), "utf-8"));
    assert.ok(assertions.length > 0, "the overridden run evaluated nothing");
  });
});

test("assess accepts a --catalog-dir signed by the --trust-root key, with no limitation (18.36 §4, §8)", () => {
  withWork("agentce-byo-trusted-", (work) => {
    const key = catalogKey(work, "signer");
    const dir = unsignedCatalogCopy(work, "trusted-cat");
    key.signDir(dir);
    const { exitCode, envelope, out } = byoAssess(work, [
      "--catalog-dir",
      dir,
      "--trust-root",
      key.trustRoot,
    ]);
    assert.equal(exitCode, 0, JSON.stringify(envelope.error));
    assert.equal("limitations" in envelope, false);
    const manifest = JSON.parse(readFileSync(join(out, "manifest.json"), "utf-8"));
    assert.equal("limitations" in manifest, false);
  });
});

test("assess refuses a --trust-root that is not a file, even with no --catalog-dir (18.36 §5, §9)", () => {
  withWork("agentce-byo-absent-root-", (work) => {
    const absent = join(work, "absent.json");
    for (const extra of [["--catalog-dir", unsignedCatalogCopy(work, "unsigned-cat")], []]) {
      const { exitCode, envelope } = byoAssess(work, [...extra, "--trust-root", absent]);
      assert.equal(exitCode, 3);
      assert.deepEqual(refusal(envelope), {
        key: "input.trust_root_not_a_file",
        cause: `the trust root '${absent}' is not an existing file.`,
        fix: "pass --trust-root <file>.",
      });
    }
  });
});

test("assess refuses a --trust-root that is not readable JSON (18.36 §6)", () => {
  withWork("agentce-byo-bad-json-", (work) => {
    const root = join(work, "bad-root.json");
    writeFileSync(root, "{not json");
    const { exitCode, envelope } = byoAssess(work, ["--trust-root", root]);
    assert.equal(exitCode, 3);
    const got = refusal(envelope);
    assert.equal(got.key, "input.trust_root_invalid");
    assert.equal(got.fix, TRUST_ROOT_FIX);
    // Only the prefix: the suffix is the JSON parser's own message, which differs per engine.
    assert.ok(
      got.cause.startsWith(
        `the trust root '${root}' could not be loaded: ${root} is not readable JSON: `,
      ),
      got.cause,
    );
  });
});

test("a validly signed but rebranded --catalog-dir is refused, override or not (18.36 §7)", () => {
  withWork("agentce-byo-rebrand-", (work) => {
    const key = catalogKey(work, "rebrand");
    const dir = unsignedCatalogCopy(work, "rebrand-cat");
    const yamlPath = join(dir, "catalog.yaml");
    writeFileSync(
      yamlPath,
      readFileSync(yamlPath, "utf-8").replace(/^id: eu-ai-act$/m, "id: eu-ai-act-rebrand"),
    );
    key.signDir(dir);
    for (const override of [[], ["--allow-unverified-catalog"]]) {
      const { exitCode, envelope } = byoAssess(work, [
        "--catalog-dir",
        dir,
        "--trust-root",
        key.trustRoot,
        ...override,
      ]);
      assert.equal(exitCode, 3);
      assert.deepEqual(refusal(envelope), {
        key: "input.catalog_mismatch",
        cause:
          "a --catalog-dir carries 'eu-ai-act-rebrand@2026.09', which --catalog did not request " +
          "('eu-ai-act@2026.09').",
        fix: "pass --catalog-dir for the catalog you named, or name the id@version the directory carries.",
      });
    }
  });
});

test("quickstart honors AGENTCE_TRUST_ROOT although it has no --trust-root flag (18.36 §10)", () => {
  withWork("agentce-byo-quickstart-env-", (work) => {
    const root = join(work, "bad-root.json");
    writeFileSync(root, "{not json");
    const previous = process.env.AGENTCE_TRUST_ROOT;
    process.env.AGENTCE_TRUST_ROOT = root;
    try {
      const { exitCode, envelope } = runJson(["quickstart", "--out", join(work, "out")]);
      assert.equal(exitCode, 3);
      assert.equal(refusal(envelope).key, "input.trust_root_invalid");
      assert.equal(refusal(envelope).fix, TRUST_ROOT_FIX);
    } finally {
      if (previous === undefined) {
        Reflect.deleteProperty(process.env, "AGENTCE_TRUST_ROOT");
      } else {
        process.env.AGENTCE_TRUST_ROOT = previous;
      }
    }
  });
});

test("assess refuses a trust root whose top level is not an object (18.36 §11)", () => {
  withWork("agentce-byo-array-root-", (work) => {
    const root = join(work, "array-root.json");
    writeFileSync(root, "[]");
    const { exitCode, envelope } = byoAssess(work, [
      "--trust-root",
      root,
      "--allow-unverified-catalog",
      "--catalog-dir",
      unsignedCatalogCopy(work, "unsigned-cat"),
    ]);
    assert.equal(exitCode, 3);
    assert.deepEqual(refusal(envelope), {
      key: "input.trust_root_invalid",
      cause: `the trust root '${root}' could not be loaded: ${root} does not hold a trust-root object`,
      fix: TRUST_ROOT_FIX,
    });
  });
});
