/**
 * Every case of the shared YAML profile table (spec/rules/yaml-profile-cases.json, SPEC 8.7; 18.184u) through the
 * real loaders: a refuse case is refused under each file role's key with a message naming the rule and line, an
 * accept case reads (Python's tests/test_yaml_profile.py, the same cases).
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { catalogMapping } from "./catalog";
import { DomainBinding } from "./domain";
import { InputError } from "./errors";
import { loadProfile } from "./profile";
import { loadDeviationRegister, loadPyyaml, pyyamlLoad } from "./readiness";
import { pyRepr } from "./util";
import { HINTS, YamlProfileError } from "./yamlProfile";

const REPO = join(__dirname, "..", "..", "..");

interface Case {
  readonly id: string;
  readonly rule: string;
  readonly expect: "accept" | "refuse";
  readonly line?: number;
  readonly yaml?: string;
  readonly generate?: { readonly kind: string; readonly depth: number };
}

const TABLE = JSON.parse(
  readFileSync(join(REPO, "spec", "rules", "yaml-profile-cases.json"), "utf-8"),
) as { hints: Record<string, string>; cases: Case[] };

/** Each file role: its key, its words, and the real loader that reads a file in that role. */
const ROLES: readonly (readonly [string, string, (path: string) => unknown])[] = [
  ["input.profile_invalid", "the applicability profile", loadProfile],
  ["input.domain_binding_invalid", "the domain binding", (path) => DomainBinding.load(path)],
  ["input.deviation_invalid", "the deviation register", loadDeviationRegister],
];

const TMP = mkdtempSync(join(tmpdir(), "agentce-yaml-profile-"));
after(() => rmSync(TMP, { recursive: true, force: true }));

function generate(kind: string, depth: number): string {
  const blockMap = (d: number): string =>
    `${Array.from({ length: d - 1 }, (_, i) => `${"  ".repeat(i)}k:\n`).join("")}${"  ".repeat(d - 1)}k: v\n`;
  switch (kind) {
    case "block_map":
      return blockMap(depth);
    case "block_seq":
      return `${Array.from({ length: depth - 1 }, (_, i) => `${"  ".repeat(i)}-\n`).join("")}${"  ".repeat(depth - 1)}- v\n`;
    case "flow_seq":
      return `${"[".repeat(depth)}${"]".repeat(depth)}\n`;
    case "flow_map":
      return `${"{a: ".repeat(depth - 1)}{}${"}".repeat(depth - 1)}\n`;
    case "mixed": {
      const half = Math.floor(depth / 2);
      const rest = depth - half;
      return `${blockMap(half).slice(0, -2)}${"[".repeat(rest)}${"]".repeat(rest)}\n`;
    }
    case "indentless":
      return `${blockMap(depth - 1).slice(0, -6)}k:\n${"  ".repeat(depth - 2)}- v\n`;
    default:
      throw new Error(kind);
  }
}

/** The case's YAML, built from its `generate` description when it has one (the table's `generators`). */
function caseText(c: Case): string {
  if (c.yaml !== undefined) {
    return c.yaml;
  }
  const g = c.generate as { kind: string; depth: number };
  return generate(g.kind, g.depth);
}

const catalogFiles = (text: string) => ({
  root: join(TMP, "cat"),
  files: new Map([["catalog.yaml", Buffer.from(text, "utf-8")]]),
  linksOut: new Set<string>(),
});

test("the table's hints are the engine's hints", () => {
  assert.deepEqual(TABLE.hints, HINTS);
});

test("every rule keeps a refuse case", () => {
  const refused = new Set(TABLE.cases.filter((c) => c.expect === "refuse").map((c) => c.rule));
  assert.deepEqual([...refused].sort(), Object.keys(HINTS).sort());
});

for (const c of TABLE.cases) {
  test(`YAML profile case ${c.id}`, () => {
    const text = caseText(c);
    const path = join(TMP, `${c.id}.yaml`);
    writeFileSync(path, Buffer.from(text, "utf-8"));
    if (c.expect === "accept") {
      assert.notEqual(pyyamlLoad(text), null);
      try {
        catalogMapping(catalogFiles(text), "catalog.yaml");
      } catch (error) {
        // Read, but not one mapping: the catalog role's own shape check, after the profile.
        assert.ok(
          error instanceof InputError && error.cause.endsWith("does not hold one YAML mapping."),
          String(error),
        );
      }
      for (const [key, what] of ROLES) {
        loadPyyaml(path, key, what);
      }
      return;
    }
    const tail = `is outside the YAML profile: ${c.rule} at line ${c.line} (SPEC 8.7)`;
    const fix = `${HINTS[c.rule]}.`;
    assert.throws(
      () => pyyamlLoad(text),
      (error: unknown) => error instanceof YamlProfileError && error.violation.message() === tail,
    );
    assert.throws(
      () => catalogMapping(catalogFiles(text), "catalog.yaml"),
      (error: unknown) => {
        assert.ok(error instanceof InputError, String(error));
        assert.equal(error.key, "input.catalog_unreadable");
        assert.equal(
          error.cause,
          `the catalog directory ${join(TMP, "cat")} holds a file that cannot be loaded: catalog.yaml ${tail}.`,
        );
        assert.equal(error.fix, fix);
        return true;
      },
    );
    for (const [key, what, load] of ROLES) {
      for (const read of [() => loadPyyaml(path, key, what), () => load(path)]) {
        assert.throws(read, (error: unknown) => {
          assert.ok(error instanceof InputError, String(error));
          assert.equal(error.key, key);
          assert.equal(error.cause, `${what} at ${pyRepr(path)} ${tail}.`);
          assert.equal(error.fix, fix);
          return true;
        });
      }
    }
  });
}
