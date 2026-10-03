/**
 * 18.34: the Portable Shape Profile forbids sh:sparql and sh:js; catalog loading refuses both.
 *
 * TypeScript has no `catalog lint` command (confirmed: no `lint` entry in `cli.ts`'s dispatch), so
 * this item's TS scope is catalog *loading* only, via `loadCatalog` -> `parseShapesTtl`.
 */

import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadCatalog } from "./catalog";
import { InputError } from "./errors";

const REPO = join(__dirname, "..", "..", "..");
const BASE = join(REPO, "spec", "catalogs", "base", "eu-ai-act");

/** Run `body` against a copy of the base catalog, `DAT-01.ttl` edited by `edit`, in a fresh temp dir. */
function withEditedCatalog(edit: (shapeText: string) => string, body: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-psp-"));
  try {
    cpSync(BASE, dir, { recursive: true });
    const shapePath = join(dir, "shapes", "DAT-01.ttl");
    writeFileSync(shapePath, edit(readFileSync(shapePath, "utf-8")), "utf-8");
    body(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Run `body` against a copy of the base catalog with `triple` appended to DAT-01's node shape. */
function withMutatedCatalog(triple: string, body: (dir: string) => void): void {
  withEditedCatalog(
    (text) => text.replace('sh:name "S1" ] .', `sh:name "S1" ] ; ${triple} .`),
    body,
  );
}

test("loadCatalog refuses a shape using sh:sparql", () => {
  withMutatedCatalog("sh:sparql [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.sparql_forbidden",
    );
  });
});

test("loadCatalog refuses a shape using sh:js", () => {
  withMutatedCatalog("sh:js [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.script_forbidden",
    );
  });
});

test("loadCatalog refuses a shape using sh:javascript", () => {
  withMutatedCatalog("sh:javascript [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.script_forbidden",
    );
  });
});

// Contract-critic round 1 (B1): a shape with BOTH predicates must always report sparql first
// (spec/rules/psp_check.py's PRIORITY_DENY), matching Python's fixed-order check, never whichever
// one N3.js happened to store first.
test("loadCatalog refuses a shape carrying both predicates as sparql deterministically", () => {
  withMutatedCatalog("sh:js [] ; sh:sparql [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.sparql_forbidden",
    );
  });
});

test("loadCatalog refuses a forbidden shape predicate regardless of case", () => {
  withMutatedCatalog("sh:SPARQL [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.sparql_forbidden",
    );
  });
  withMutatedCatalog("sh:JavaScript [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.script_forbidden",
    );
  });
});

test("loadCatalog still loads the unmutated base catalog clean", () => {
  assert.doesNotThrow(() => loadCatalog(BASE));
});

test("loadCatalog refuses a forbidden predicate under an aliased prefix or bare IRI", () => {
  withEditedCatalog(
    (text) =>
      text
        .replace(
          "@prefix sh: <http://www.w3.org/ns/shacl#> .",
          "@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix shacl: <http://www.w3.org/ns/shacl#> .",
        )
        .replace('sh:name "S1" ] .', 'sh:name "S1" ] ; shacl:sparql [] .'),
    (dir) => {
      assert.throws(
        () => loadCatalog(dir),
        (err: unknown) => err instanceof InputError && err.key === "catalog.shape.sparql_forbidden",
      );
    },
  );

  withMutatedCatalog("<http://www.w3.org/ns/shacl#js> [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.script_forbidden",
    );
  });
});

test("loadCatalog does not refuse a literal or a comment that merely mentions sh:sparql", () => {
  withEditedCatalog(
    (text) =>
      text.replace(
        'sh:name "S1" ] .',
        'sh:name "S1, not sh:sparql or sh:js (documentation only)" ] .',
      ),
    (dir) => {
      assert.doesNotThrow(() => loadCatalog(dir));
    },
  );

  withEditedCatalog(
    (text) => `# this shape must never use sh:sparql or sh:js\n${text}`,
    (dir) => {
      assert.doesNotThrow(() => loadCatalog(dir));
    },
  );
});

test("loadCatalog refuses a forbidden predicate nested inside a property shape", () => {
  withEditedCatalog(
    (text) =>
      text.replace(
        'sh:property [ sh:path prov:used ; sh:minCount 1 ; sh:name "S1" ] .',
        'sh:property [ sh:path prov:used ; sh:minCount 1 ; sh:name "S1" ; sh:sparql [] ] .',
      ),
    (dir) => {
      assert.throws(
        () => loadCatalog(dir),
        (err: unknown) => err instanceof InputError && err.key === "catalog.shape.sparql_forbidden",
      );
    },
  );
});

test("loadCatalog refuses a forbidden predicate written as a Turtle unicode escape", () => {
  // Turtle's IRIREF grammar allows \uXXXX/\UXXXXXXXX escapes inside <...>; a predicate scan
  // that compared raw, undecoded IRI text would miss this, since it is the same IRI as sh:sparql
  // once decoded.
  withMutatedCatalog("<http://www.w3.org/ns/shacl#sp\\u0061rql> [] ", (dir) => {
    assert.throws(
      () => loadCatalog(dir),
      (err: unknown) => err instanceof InputError && err.key === "catalog.shape.sparql_forbidden",
    );
  });
});
