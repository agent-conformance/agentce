/** `agentce assess <folder>`'s records scan, as Python's `agentce.records` does it: walk order, what is
 * skipped and named, each refusal, the depth and lone-surrogate rules, JSON Lines splitting, the
 * derived profile and the round trip (VG-RECORDS-FOLDER-PARITY compares the two engines end to end). */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { main } from "./cli";
import { AgentceError } from "./errors";
import { profileFromDict } from "./profile";
import {
  DEFAULT_SUBJECT,
  MAX_RECORD_DEPTH,
  rawDepth,
  recordsLines,
  recordsProfile,
  recordsSubject,
  scanRecords,
} from "./records";

const FIXTURES = join(__dirname, "..", "..", "..", "adapters", "otel-genai", "fixtures");
const SESSION = readFileSync(join(FIXTURES, "otel-genai-agent-session", "input.json"), "utf-8");
const UNDERWRITER = "spiffe://corp/agents/credit-underwriter";
const FRAUD = "spiffe://corp/agents/fraud-detection-agent";

type Doc = { resourceSpans: { scopeSpans: { spans: Record<string, unknown>[] }[] }[] };

/** The agent-session fixture with every span on trace `digit` x 32, so its events are its own. */
function session(digit: string): Doc {
  const doc = JSON.parse(SESSION) as Doc;
  for (const rs of doc.resourceSpans) {
    for (const ss of rs.scopeSpans) {
      for (const span of ss.spans) {
        span.traceId = digit.repeat(32);
      }
    }
  }
  return doc;
}

const made: string[] = [];
after(() => {
  for (const dir of made) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-records-"));
  made.push(dir);
  return dir;
}

/** `text` with `old` replaced once; fails if `old` is not there, so a case cannot pass vacuously. */
function swap(text: string, old: string, replacement: string): string {
  assert.ok(text.includes(old), old);
  return text.replace(old, replacement);
}

function folder(files: Record<string, string | Buffer>): string {
  const root = tempDir();
  for (const [rel, data] of Object.entries(files)) {
    const path = join(root, rel);
    mkdirSync(join(path, ".."), { recursive: true });
    writeFileSync(path, data);
  }
  return root;
}

/** `doc` with its first span's attributes holding an entry nested so the raw depth is `target`. */
function nested(target: number, digit: string): string {
  const doc = session(digit);
  const entry: Record<string, unknown> = { key: "probe", value: { stringValue: "x" }, extra: [] };
  (doc.resourceSpans[0]?.scopeSpans[0]?.spans[0]?.attributes as unknown[]).push(entry);
  let deep: unknown[] = [];
  let text = JSON.stringify(doc);
  while (rawDepth(Buffer.from(text)) < target) {
    deep = [deep];
    entry.extra = deep;
    text = JSON.stringify(doc);
  }
  assert.equal(rawDepth(Buffer.from(text)), target);
  return text;
}

function refusal(fn: () => unknown): string {
  try {
    fn();
  } catch (err) {
    assert.ok(err instanceof AgentceError, String(err));
    return err.key;
  }
  return "no refusal";
}

test("files are walked in code-point order, files before folders, hidden names and symlinked folders skipped", () => {
  const root = folder({
    "b.json": JSON.stringify(session("1")),
    "a\u{1F600}.json": JSON.stringify(session("2")),
    "aＡ.json": JSON.stringify(session("3")),
    "sub/x.json": JSON.stringify(session("4")),
    "__pycache__/z.json": JSON.stringify(session("5")),
    ".hidden.json": JSON.stringify(session("6")),
    ".git/y.json": JSON.stringify(session("7")),
    "c.gz": "x",
    "notes.txt": "hello",
  });
  const elsewhere = folder({ "w.json": JSON.stringify(session("8")) });
  symlinkSync(elsewhere, join(root, "linked"));
  const scanned = scanRecords(root);
  // U+FF21 sorts before U+1F600 by code point (Python's `sorted`), after it by UTF-16 unit.
  assert.deepEqual(
    scanned.summary.files.map((f) => f.path),
    ["aＡ.json", "a\u{1F600}.json", "b.json", "__pycache__/z.json", "sub/x.json"],
  );
  assert.deepEqual(scanned.summary.unrecognised, [
    { path: "linked", reason: "a symlinked folder is not followed" },
    { path: "c.gz", reason: "compressed files are not read; decompress them first" },
  ]);
});

test("a symlink that leaves the folder is named, never followed", () => {
  const outside = folder({ "s.json": SESSION });
  const root = folder({ "ok.json": SESSION });
  symlinkSync(join(outside, "s.json"), join(root, "link.json"));
  assert.deepEqual(scanRecords(root).summary.unrecognised, [
    { path: "link.json", reason: "a symlink that leaves the folder is not read" },
  ]);
});

test("an out folder inside the records folder, and a previous run's bundle in it, are not read", () => {
  const root = folder({
    "session.json": SESSION,
    "out/records-bundle/manifest.json": "{}",
    "out/records-bundle/events/e.jsonl": "{}\n",
    "out/assertions.json": "[]",
  });
  const scanned = scanRecords(root, { exclude: join(root, "out") });
  assert.deepEqual(
    scanned.summary.files.map((f) => f.path),
    ["session.json"],
  );
  assert.deepEqual(scanned.summary.unrecognised, []);
});

test("every records_out_collides case is refused before anything is read", () => {
  const root = folder({ "session.json": SESSION, "full/keep.txt": "mine" });
  assert.equal(
    refusal(() => scanRecords(root, { exclude: root })),
    "input.records_out_collides",
  );
  assert.equal(
    refusal(() => scanRecords(root, { exclude: join(root, "full") })),
    "input.records_out_collides",
  );
  const stale = folder({ "records-bundle/keep.txt": "mine" });
  assert.equal(
    refusal(() => scanRecords(root, { exclude: stale })),
    "input.records_out_collides",
  );
  const linked = folder({});
  symlinkSync(folder({ "manifest.json": "{}" }), join(linked, "records-bundle"));
  assert.equal(
    refusal(() => scanRecords(root, { exclude: linked })),
    "input.records_out_collides",
  );
  // An empty out folder inside the records folder is fine.
  mkdirSync(join(root, "empty"));
  assert.equal(scanRecords(root, { exclude: join(root, "empty") }).summary.files.length, 1);
});

test("a folder with no GenAI span, or nothing recognised, is refused with its own key", () => {
  const http = {
    resourceSpans: [
      {
        scopeSpans: [
          {
            spans: [
              {
                traceId: "a".repeat(32),
                spanId: "b".repeat(16),
                name: "GET /",
                startTimeUnixNano: "1700000000000000000",
                endTimeUnixNano: "1700000001000000000",
                attributes: [{ key: "http.method", value: { stringValue: "GET" } }],
              },
            ],
          },
        ],
      },
    ],
  };
  assert.equal(
    refusal(() => scanRecords(folder({ "http.json": JSON.stringify(http) }))),
    "input.records_no_genai_spans",
  );
  assert.equal(
    refusal(() => scanRecords(folder({ "a.json": "{}", "b.jsonl": "[1]\n", "c.gz": "x" }))),
    "input.records_none_recognised",
  );
});

test("a profile with no subject, or one subject twice, is refused as profile_invalid", () => {
  const window = { start: "2024-01-01T00:00:00Z", end: "2026-01-01T00:00:00Z" };
  const none = profileFromDict({ profile_version: 1, observation_window: window, subjects: [] });
  const twice = profileFromDict({
    profile_version: 1,
    observation_window: window,
    subjects: [{ id: FRAUD }, { id: FRAUD }],
  });
  assert.equal(
    refusal(() => recordsSubject(none)),
    "input.profile_invalid",
  );
  assert.equal(
    refusal(() => recordsSubject(twice)),
    "input.profile_invalid",
  );
});

test("a document nested 256 deep is read and one nested 257 deep is not, also in a JSON Lines line and behind a BOM", () => {
  assert.equal(MAX_RECORD_DEPTH, 256);
  const bom = Buffer.from([0xef, 0xbb, 0xbf]);
  const root = folder({
    "ok256.json": nested(256, "1"),
    "deep.json": nested(257, "2"),
    "bom257.json": Buffer.concat([bom, Buffer.from(nested(257, "3"))]),
    "nest.jsonl": `${nested(256, "4")}\n${nested(257, "5")}\n`,
    "brackets.json": swap(
      JSON.stringify(session("6")),
      '"credit-underwriter"}',
      `"credit-underwriter", "probe": "\\"${"[".repeat(300)}"}`,
    ),
  });
  const { summary } = scanRecords(root);
  assert.deepEqual(
    summary.files.map((f) => f.path),
    ["brackets.json", "nest.jsonl", "ok256.json"],
  );
  assert.deepEqual(
    recordsLines(summary, undefined).filter((l) => l.startsWith("not read: ")),
    [
      "not read: bom257.json: nested too deeply to parse safely",
      "not read: deep.json: nested too deeply to parse safely",
      "not read: nest.jsonl line 2: nested too deeply to parse safely",
    ],
  );
});

test("a lone surrogate the events would carry names the file; a valid pair or a dropped attribute is read", () => {
  const text = (digit: string) => JSON.stringify(session(digit));
  const root = folder({
    "lone-service.json": swap(text("1"), '"credit-underwriter"}', '"cr\\ud800edit"}'),
    "lone-agent.json": swap(text("2"), `"${UNDERWRITER}"`, '"spiffe://corp/agents/cr\\udc00"'),
    "pair.json": swap(text("3"), '"credit-underwriter"}', '"cr\\ud83d\\ude00edit"}'),
    // An attribute the adapter never maps: the lone surrogate never reaches an event.
    "dropped.json": swap(
      text("4"),
      '{"key":"gen_ai.operation.name"',
      '{"key":"probe","value":{"stringValue":"x\\ud800"}},{"key":"gen_ai.operation.name"',
    ),
    "latin1.json": Buffer.from([0x7b, 0x22, 0xff, 0x22, 0x3a, 0x31, 0x7d]),
    "empty.json": "",
    "junk.json": "{not json",
  });
  const { summary } = scanRecords(root);
  assert.deepEqual(
    summary.files.map((f) => f.path),
    ["dropped.json", "pair.json"],
  );
  const lone = "invalid_encoding: a string holds a lone surrogate, which UTF-8 cannot carry";
  assert.deepEqual(summary.unrecognised, [
    { path: "empty.json", reason: "invalid_json: not valid JSON" },
    { path: "junk.json", reason: "invalid_json: not valid JSON" },
    { path: "latin1.json", reason: "invalid_encoding: not valid UTF-8" },
    { path: "lone-agent.json", reason: lone },
    { path: "lone-service.json", reason: lone },
  ]);
});

test("JSON Lines split at CRLF, CR and LF, skip blank lines, and list 20 bad lines while counting all", () => {
  const flat = JSON.stringify(session("1"));
  const root = folder({
    "bad.jsonl": `${Array(25).fill("not json").join("\n")}\n${flat}\n`,
    "crlf.jsonl": `\r\n${JSON.stringify(session("2"))}\r\n  \r\nnope\r${JSON.stringify(session("3"))}\r`,
  });
  const { summary } = scanRecords(root);
  assert.deepEqual(
    summary.files.map((f) => [f.path, f.spans]),
    [
      ["bad.jsonl", 5],
      ["crlf.jsonl", 10],
    ],
  );
  assert.equal(summary.lines_unrecognised, 26);
  assert.equal(summary.unrecognised_lines.length, 20);
  assert.deepEqual(summary.unrecognised_lines[19], {
    path: "bad.jsonl",
    line: 20,
    reason: "invalid_json: not valid JSON",
  });
});

test("the derived profile names each agent by its own id, with the tools and models it saw", () => {
  const scanned = scanRecords(folder({ "session.json": SESSION }));
  const profile = scanned.profile();
  assert.deepEqual(Object.keys(profile), [
    "profile_version",
    "observation_window",
    "pilot_window",
    "catalogs",
    "subjects",
  ]);
  assert.equal(profile.pilot_window, true);
  const [subject] = profile.subjects as Record<string, unknown>[];
  assert.equal(subject?.id, UNDERWRITER);
  assert.equal(subject?.role, "deployer");
  assert.ok((subject?.declared_tools as string[]).includes("credit_bureau_lookup"));
  assert.deepEqual(scanned.subjects, [UNDERWRITER]);
});

test("a passed-back profile with several subjects keeps id-less records off the named agents", () => {
  const root = folder({
    "session.json": SESSION,
    "rag.json": readFileSync(join(FIXTURES, "openinference-rag", "input.json")),
  });
  const first = scanRecords(root);
  const derived = profileFromDict(first.profile());
  assert.equal(recordsSubject(derived), UNDERWRITER);
  const two = profileFromDict({
    ...first.profile(),
    subjects: [...(first.profile().subjects as object[]), { id: FRAUD }],
  });
  assert.equal(recordsSubject(two), undefined);
  const scanned = scanRecords(root, { perAgent: true });
  assert.deepEqual(scanned.subjects, [UNDERWRITER, DEFAULT_SUBJECT]);
  const evaluated = recordsProfile(two, scanned);
  assert.deepEqual(
    evaluated.subjects.map((s) => s.id),
    [UNDERWRITER, FRAUD, DEFAULT_SUBJECT],
  );
  assert.deepEqual(evaluated.subjects[2]?.declaredTools, []);
});

function runJson(argv: string[]): { exitCode: number; envelope: Record<string, unknown> } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (line: string) => {
    lines.push(line);
  };
  let exitCode: number;
  try {
    exitCode = main([...argv, "--json"]);
  } finally {
    console.log = original;
  }
  return { exitCode, envelope: JSON.parse(lines.join("\n")) };
}

function errorKey(envelope: Record<string, unknown>): unknown {
  return (envelope.error as Record<string, unknown> | undefined)?.message_key;
}

test("assess refuses a missing folder, a folder with --bundle and --package-for-sharing", () => {
  const root = folder({ "session.json": SESSION });
  const out = join(tempDir(), "out");
  const cases: [string[], string][] = [
    [["assess", join(root, "nope"), "--out", out], "input.records_not_a_directory"],
    [["assess", root, "--bundle", root, "--out", out], "input.records_source_ambiguous"],
    [["assess", root, "--package-for-sharing", "--out", out], "input.package_requires_bundle"],
  ];
  for (const [argv, key] of cases) {
    const { exitCode, envelope } = runJson(argv);
    assert.equal(exitCode, 3, key);
    assert.equal(errorKey(envelope), key);
  }
});

test("the derived profile passed back gives the same assertions and writes no profile", () => {
  const root = folder({ "session.json": SESSION });
  const work = tempDir();
  const first = runJson(["assess", root, "--out", join(work, "first")]);
  assert.notEqual(first.exitCode, 2);
  const profile = join(work, "first", "applicability.yaml");
  const second = runJson(["assess", root, "--profile", profile, "--out", join(work, "second")]);
  assert.equal(second.exitCode, first.exitCode);
  assert.deepEqual(
    readFileSync(join(work, "second", "assertions.json")),
    readFileSync(join(work, "first", "assertions.json")),
  );
  assert.throws(() => readFileSync(join(work, "second", "applicability.yaml")));
});
