/**
 * The no-learned-components check for the TypeScript engine (SPEC §8.7, HR-1/HR-2).
 *
 * The engine and every evaluation-path dependency must resolve to a tree with no machine-learning
 * framework and no LLM or embedding client. This scans the engine's own `pnpm-lock.yaml` for any
 * package whose name matches an entry in the shared denylist `spec/rules/no-ml-denylist.txt`, compared
 * by exact PEP 503 normalisation (lowercase; runs of `- _ .` collapsed to one `-`), never as a
 * substring — so `linkml`, `js-yaml`, or `html5lib` are never mistaken for a learned component. Scoped
 * package names are matched on both the scope and the sub-name. `claim: full` requires `result: pass`.
 */

import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { byteCompare } from "./util";

function normalize(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[-_.]+/g, "-");
}

function loadDenylist(repoRoot: string): Set<string> {
  const text = readFileSync(join(repoRoot, "spec", "rules", "no-ml-denylist.txt"), "utf-8");
  const out = new Set<string>();
  for (const raw of text.split("\n")) {
    const line = (raw.split("#")[0] as string).trim();
    if (line) {
      out.add(normalize(line));
    }
  }
  return out;
}

/** The normalised name(s) a lockfile package key contributes (scope and sub-name for scoped names). */
function candidates(key: string): string[] {
  const sep = key.startsWith("@") ? key.indexOf("@", 1) : key.indexOf("@");
  const name = sep > 0 ? key.slice(0, sep) : key;
  const out = [normalize(name)];
  if (name.startsWith("@") && name.includes("/")) {
    const [scope, sub] = name.slice(1).split("/");
    out.push(normalize(scope as string), normalize(sub as string));
  }
  return out;
}

/**
 * Extract every locked package name from a `pnpm-lock.yaml`. The pnpm-12 lockfile is a multi-document
 * YAML, so it is scanned line-wise rather than parsed: a package key is a two-space-indented mapping
 * key that carries an `@version` and nothing after the colon (e.g. `  n3@2.7.12:` or
 * `  '@types/node@22.7.5':`), distinguishing it from settings and importer keys.
 */
function packageNames(lockPath: string): Set<string> {
  const names = new Set<string>();
  const keyLine = /^ {2}(?:'([^']+)'|([^\s].*?)):\s*$/;
  for (const raw of readFileSync(lockPath, "utf-8").split("\n")) {
    const match = keyLine.exec(raw);
    const key = match ? (match[1] ?? match[2]) : undefined;
    if (key?.includes("@")) {
      for (const candidate of candidates(key)) {
        names.add(candidate);
      }
    }
  }
  return names;
}

export interface NoMlResult {
  result: "pass" | "fail";
  packages: number;
  violations: string[];
}

/** Scan the engine's lockfile against the denylist; `result: pass` iff no denylisted package is present. */
export function evaluateNoMl(repoRoot: string, lockPath: string): NoMlResult {
  const deny = loadDenylist(repoRoot);
  const names = packageNames(lockPath);
  const violations = [...names].filter((name) => deny.has(name)).sort(byteCompare);
  return {
    result: violations.length === 0 ? "pass" : "fail",
    packages: names.size,
    violations,
  };
}

export interface InstalledNoMlResult {
  result: "pass" | "fail";
  denylisted_present: string[];
}

/**
 * Whether `name` (already normalised) resolves from `resolveFrom` via Node's own module
 * resolution -- the "is this actually present in the dependency tree the CLI runs with" question
 * `installed_distribution_names()` answers in Python, phrased as presence-of-each-denylisted-name
 * so it works under npm's hoisting (a `node_modules` walk from the package's own directory would
 * miss hoisted deps; module resolution does not).
 *
 * Presence is checked via `require.resolve.paths` (which lists candidate directories without
 * importing anything) plus `existsSync` on each candidate's `<name>/package.json`, never a bare
 * `require.resolve(name)` -- the plain resolver throws `ERR_PACKAGE_PATH_NOT_EXPORTED` for a
 * package present but ESM-only or lacking a CJS `"."` export, and treating that as "absent" would
 * produce a false `no_ml: pass` (a hard-invariant violation). A scoped name (`@scope/name`) is also
 * detected when only its scope directory is present, mirroring `candidates()`'s scope-and-sub-name
 * split above.
 */
function isInstalled(name: string, resolveFrom: string): boolean {
  const req = createRequire(join(resolveFrom, "noop.js"));
  let dirs: string[];
  try {
    dirs = req.resolve.paths(name) ?? [];
  } catch {
    dirs = [];
  }
  for (const dir of dirs) {
    if (existsSync(join(dir, name, "package.json"))) {
      return true;
    }
    if (name.startsWith("@") && name.includes("/")) {
      const scope = name.split("/")[0] as string;
      if (existsSync(join(dir, scope))) {
        return true;
      }
    }
  }
  return false;
}

/** The installed engine's own no_ml self-report (`agentce version --json`): every name in
 * `denylist` that actually resolves from `resolveFrom` -- the package's own installed location --
 * via Node's module resolution, not a lockfile the installed artifact cannot see. */
export function evaluateInstalledNoMl(
  denylist: ReadonlySet<string>,
  resolveFrom: string,
): InstalledNoMlResult {
  const present = [...denylist].filter((name) => isInstalled(name, resolveFrom)).sort(byteCompare);
  return { result: present.length === 0 ? "pass" : "fail", denylisted_present: present };
}

/** The vendored denylist copy an installed package carries (`data/no-ml-denylist.txt`,
 * byte-identical to `spec/rules/no-ml-denylist.txt`, `noMl.test.ts` holds them in sync), for the
 * installed self-report above -- unlike `loadDenylist`, this needs no repo checkout. */
export function loadVendoredDenylist(): Set<string> {
  const text = readFileSync(join(__dirname, "..", "data", "no-ml-denylist.txt"), "utf-8");
  const out = new Set<string>();
  for (const raw of text.split("\n")) {
    const line = (raw.split("#")[0] as string).trim();
    if (line) {
      out.add(normalize(line));
    }
  }
  return out;
}
