/**
 * The graph builder is byte-identical to the Python reference (SPEC §6.3).
 *
 * `testdata/graph-golden.txt` is the reference engine's full triple dump for `graph-fixture.json`
 * (edges, literals, and the subclass closure, each line sorted by UTF-8 bytes). The TypeScript
 * builder must reproduce it exactly — every materialised glue edge, principal HMAC, and closure pair.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { DomainBinding } from "./domain";
import { buildGraph } from "./graph";

const TESTDATA = join(__dirname, "..", "testdata");

test("graph triples match the reference golden byte for byte", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "graph-fixture.json"), "utf-8"));
  const golden = readFileSync(join(TESTDATA, "graph-golden.txt"), "utf-8");
  const store = buildGraph(fixture.events, { domain: DomainBinding.fromDict(fixture.domain) });
  assert.equal(`${store.dumpTriples().join("\n")}\n`, golden);
});

test("graph materialises the human chain terminus and precedence", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "graph-fixture.json"), "utf-8"));
  const store = buildGraph(fixture.events, { domain: DomainBinding.fromDict(fixture.domain) });
  // dec-1 has a human overseer at its chain terminus, and is reviewed, so dec-2 is precededBy dec-1.
  assert.equal(
    store.objects("agentce:event/dec-2", "agentce:precededBy")[0],
    "agentce:event/dec-1",
  );
  const terminus = store.objects("agentce:event/dec-1", "agentce:chainTerminus")[0] as string;
  assert.equal(store.isA(terminus, "agentce:HumanPrincipal"), true);
});
