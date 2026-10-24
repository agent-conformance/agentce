# Verification suite changelog

Every change to a build gate (added, changed or retired) is recorded here, newest first. The registry lint
fails if a gate in `gates.json` is not named in this file.

## 0.128.0

- `VG-EUAIACT-CLUSTER4` (18.37h, changed) runs 96 cases (was 76). TRN-03 now checks the two explanation pointers the
  model defines: a consequential decision's `rationale_claim_ref` and the `content_ref` of the notice that informed
  the person. Written as an event ref, each must name a held event; a Digest, a URI or a bare id names no event and is
  not judged. One clean notice is enough, and a dangling rationale fails whatever the notices say. Twenty new cases
  and three new seeded faults, one per engine.
- The demo-fault estimate for `VG-EUAIACT-CLUSTER4` is scaled to 2008 s for 35 runs over 96 cases until a run
  measures it. The CI `demo-fault` job runs on 49 lanes (was 48): at 48 the heaviest shard was 12.1 minutes, over the
  12.0 lane bound.

## 0.127.0

- `VG-EUAIACT-CLUSTER4` (18.37g, new, tier quick) runs 76 cases through the real Python, TypeScript and Java CLIs.
  The last eleven eu-ai-act controls that shared a rule now each test what their title says. REC-03 needs every input
  a consequential decision names to be a held record, and at least one. DAT-01 needs one of those inputs to be a data
  read, a ResourceAccess read or a MemoryRead. TRN-02 needs the decision to record the AI system's role in it
  (`ai_role`). INT-04 needs an integrity block on the call at a strength SPEC 6.6 can verify for an enforcement point,
  or any block for an independent system. ROB-01 needs the call's effect classified. ROB-04 needs a held, verified
  delegation issued at or before the call and expiring after it, compared as instants. ROB-03 names the references it
  checks. DAT-02 and DOC-04 move to rung 3 and read not_assessed, since no record shows their measurement. Thirty-one
  seeded faults: thirteen in Python, nine each in TypeScript and Java.
- `VG-CATALOG-RULE-UNIQUE` (18.37g, changed): the last six eu-ai-act pairs leave the baseline, every pair list is empty
  and every ceiling is 0.
- The CI `demo-fault` job runs on 48 lanes (was 45): with VG-EUAIACT-CLUSTER4's thirty-one faults, estimated at 1453 s
  from VG-EUAIACT-CLUSTER2's measured time, the heaviest of 45 shards was 13.4 minutes, over the 12.0 lane bound.
- `VG-EUAIACT-CLUSTER1` and `VG-EUAIACT-CLUSTER3` (18.37g, changed): every case decision records the AI system's role,
  so TRN-02 holds and each case keeps its own assess exit code.

## 0.126.0

- `VG-EUAIACT-CLUSTER3` (18.37f, new, tier quick) runs 107 cases through the real Python, TypeScript and Java CLIs.
  Eight eu-ai-act controls shared three rules. DOC-02, REC-02 and TRN-04 all checked that a consequential decision had
  a time; OVS-05, REC-05 and RSK-01 all checked that it had an authorization edge; TRN-01 and TRN-05 both checked that
  it had a notification edge. Each now tests what its title says. REC-02 keeps the time. DOC-02 also needs the option
  the decision chose. TRN-04 needs an explanation Notice delivered at or after the decision and cites Art. 86. OVS-05
  needs a held PolicyDecision that gated the decision and did not deny it. RSK-01 needs that gate cleared, by allow or
  by a require_approval a human approved. REC-05 needs the gate to name its policy id, version and digest. TRN-01 needs
  a Notice that reaches the affected person. TRN-05 needs an AI-interaction Disclosure in the decision's session at or
  before it, for every decision that does not say it affects no natural person. Thirty seeded faults, ten per engine.
- `VG-CATALOG-RULE-UNIQUE` (18.37f, changed): five eu-ai-act pairs leave the baseline (DOC-02 with REC-02 and TRN-04,
  OVS-05 with REC-05 and RSK-01, TRN-01 with TRN-05), and the eu-ai-act ceiling drops from 11 to 6.
- The CI `demo-fault` job runs on 45 lanes (was 42): with VG-EUAIACT-CLUSTER3's thirty faults, estimated at 1982 s
  from VG-EUAIACT-CLUSTER2's measured time, the heaviest of 42 shards was 13.5 minutes, over the 12.0 lane bound.

## 0.125.0

- `VG-REPORT-VALIDATE` (18.60, changed): every engine reads report files by the SPEC 8.7 report-file reader
  contract. The gate now builds the TypeScript and Java engines and runs `tools/report_validate_reader_census.py`,
  which checks `spec/report/reader-cases.json` keeps a case for every reader class and every command that reaches
  it, and `tools/report_validate_parity_check.py --reader-cases`, which runs each case through `report --validate`,
  `readiness`, `sign` or `diff` in all three engines. Each must give the case's exit, key and named rule at the same
  lines and leave the folder unchanged. Six more seeded faults: Python's CSV reader accepting text after a closing
  quote, TypeScript's splitting records on LF only, Java's XML check accepting a DOCTYPE, Python's readiness answering
  NOT READY instead of refusing with exit 3, Python's sign reading `manifest.json` without the JSON profile, and the
  table losing its only `csv_unclosed_quote` case.

## 0.124.0

- `VG-OUT-LINK-CONTAINMENT` (18.184zd, changed): an `--out` the engine cannot look at is refused
  `input.out_dir_unwritable`, never `internal.unexpected`. The check now also runs `quickstart --out` under a folder
  that cannot be entered (skipped as root) and with a too-long name in all three engines. The link guard 18.184s added
  raised on these in Python and TypeScript. An `--out`, or a folder under it, that can be written but not listed is
  now refused `input.out_dir_unwritable` too, since it could hide a link leading outside (all three engines wrote
  through one); the check runs a mode-300 `--out` holding such a link. Five more seeded faults (the Python and
  TypeScript guards raising again, and each engine's guard skipping a folder it cannot list).

## 0.123.0

- `VG-OUT-LINK-CONTAINMENT` (18.184s, added): a run never writes through a link in `--out` that leads outside it
  (SPEC 8.7, ruling R7). `tools/out_link_containment_check.py` runs the real Python, TypeScript and Java
  `quickstart --out` with `integrity.jsonl`, `manifest.json` or the folder `packs` linked outside; each must exit 3
  with `input.out_link_outside` naming the entry, leave the outside file or folder unchanged and write nothing into
  `--out`. Three seeded faults (each engine's guard skipped).
- `VG-JSON-PROFILE` (18.184s, changed): two more CLI rows, a malformed and a non-UTF-8 `state.json`, refused
  `input.state_unreadable` with `--out` left holding no file in all three engines. One more seeded fault (Python
  writes to `--out` before it reads `state.json`).

## 0.122.0

- `VG-QUARANTINE-NO-PASS` (18.184r, changed): an OVS-07 target whose `@context` is `1.5` has no canonical form
  (SPEC 6.7); every engine quarantines it `schema_invalid` at admission, so the control reads insufficient_evidence,
  never conformant and never `internal.unexpected` from the integrity stage. One more seeded fault (Python admits it).
- `VG-COVERAGE-UNKNOWN` (18.184r, changed): a coverage denominator manifest whose root is not a JSON object (`[1, 2]`)
  is refused `input.coverage_denominator_manifest_invalid` with exit 3 in all three engines, where it was read as
  declaring no counts. One more seeded fault (TypeScript reads the root as no counts).
- `VG-VERIFY` (18.184r, changed): its TypeScript seeded fault searches the bounded member read `loadBundle` now does.

## 0.121.0

- `VG-JSON-PROFILE` (18.184x, added): every engine reads JSON under the SPEC 8.7 profile. The gate runs
  `tools/json_parity_check.py`, which holds `spec/report/json-cases.json` to its shape (a row for every rule, the
  `1e+20`, `5.0` and `-0.0` renderings, `1` vs `true` unequal and `1` vs `1.0` equal) and feeds every row to each
  engine's `json-cases` seam, comparing each result with the table's; then seven real-CLI rows (a manifest with a
  byte-order mark, a repeated `files` key, a coverage count of `1e20`, `diff` on NaN, `report --from` with a `rung`
  of `1.5`, a `state_version` of `true`, `sign` on a claim holding `5.0`); then the `json-profile-` cases of
  `VG-QUARANTINE-NO-PASS`. Four seeded faults.
- `VG-QUARANTINE-NO-PASS` (18.184x, changed): three cases put the write that makes ROB-02 non-conformant outside
  the JSON profile (a repeated key whose last value reads trusted, a NaN, a lone surrogate); each must read
  insufficient_evidence with a quarantine entry naming the rule. `QUARANTINE_NO_PASS_ONLY=<prefix>` runs a subset.
- `VG-TRUST-ROOT-PARSE-PARITY`, `VG-JSON-INTEGER-LIMIT` and `VG-RECORDS-FOLDER-PARITY` (18.184x, changed): a
  trust root with one leading byte-order mark is read, and refusals carry the profile wording
  `outside the JSON profile: <rule> at line <n> (SPEC 8.7)`, with a records line's rule at its file line.

## 0.120.0

- `VG-ARGV-GRAMMAR` (18.184w, added): every engine reads its command line under the SPEC 8.5 grammar. The gate
  checks that every rule in `spec/cli/argv-grammar.json` keeps a case in `spec/cli/argv-cases.json` (the registry
  lint now fails a rule with none), runs `tools/argv_grammar.py --check` over the vendored copies and the engines'
  option spellings, runs each engine's case-table test, and runs every case through the three real CLIs with
  `tools/argv_parity_check.py`, which also checks that a refused run, a refused sign included, writes nothing.
- `VG-CLI-ASSESS`, `VG-CLI-REPORT`, `VG-CLI-UTILITY`, `VG-CLI-COLLECT-DOCTOR-INIT`, `VG-DEVIATIONS-PARITY` and
  `VG-FAIL-ON-PARITY` (18.184w, changed): an option given twice is refused in every engine, where these gates had
  expected the last one to win. `VG-CLI-UTILITY`'s diff fault now drops the `=` form of `--format`, since a
  repeated `--format` no longer reaches the command.
- `VG-VERIFY-CENSUS-SHARD-COVERAGE` (18.184w, changed): `quickstart.yml` joins the fast tier, so the expected
  triggers are push to main and to `phase/**`, pull request and manual dispatch, and the workflow's root
  `concurrency:` must be the fast tier's cancel-in-progress block. The paths-ignore seeded fault searches for the new
  `branches: [main, "phase/**"]` line. The census puts `--json` before a `--` in a mutation's argv, since after `--`
  every token is a positional.

## 0.119.0

- `VG-YAML-PROFILE` (18.184u, added): every engine reads YAML under the SPEC 8.7 profile. The gate checks that the
  shared case table `spec/rules/yaml-profile-cases.json` keeps a refuse case for each rule, then runs each engine's
  case-table test, which reads every case through that engine's real loading path: an out-of-profile case is refused
  with the file role's key and a message naming the rule and line, and an in-profile case reads.
- `VG-RECORDS-PROFILE-ROUND-TRIP` and `VG-COVERAGE-UNKNOWN` (18.184u, changed): the profiles each gate builds are
  written without anchors, so the files stay inside the SPEC 8.7 profile the engines now read them under.

## 0.118.0

- `VG-FAST-TIER-REPORT-FILE-PARITY` (18.184v, added): `three-engine.yml`, which runs on every phase push, runs the
  cross-engine readiness, sign and report --validate agreement checks from source in its `report-file-parity` job.
  The gate holds that job's wiring: phase pushes trigger it, nothing can skip or excuse it, and it finishes within
  20 minutes.
- `VG-CATALOG-SHAPE-SAFETY`, `VG-I18N-ERROR-CATALOGUE`, `VG-I18N-ONE-CATALOGUE`, `VG-TRUST-ROOT-PARSE-PARITY` and
  `VG-VERIFY-REFUSAL-KEYS` (18.184v, changed): each installs the TypeScript engine's dependencies before it runs a
  TypeScript test file. They had relied on an earlier gate in the same CI shard to install them, so adding a gate
  that moved `VG-CATALOG-SHAPE-SAFETY` to another shard failed it with `tsx` not found.

## 0.117.0

- `VG-JSON-INTEGER-LIMIT` (18.184q, changed): two seeded faults follow the code they break. Python's
  `runtime_drift.jsonl` line check now reads each line with `load_report_json` and requires an object, so its fault
  puts `json.loads` back at that line; TypeScript's `report --validate` now reads every artifact with
  `parseReportJson`, so its fault drops the digit limit in `json.ts`'s reader instead of in the manifest-only
  `parseJsonText`.
- `VG-DEMO-SHARD-COVERAGE` (CI tiers, changed): the check also reads `verification-quick.yml`, the fast tier that runs
  the quick tier on every phase push, and holds its `quick-tier` job to the same exact wiring as `verification.yml`'s.
- `VG-VERIFY-CENSUS-SHARD-COVERAGE` (CI tiers, changed): the expected triggers of `quickstart.yml` are now push to
  main, pull request and manual dispatch, and the paths-ignore seeded fault searches for the
  new `branches: [main]` line.

## 0.116.0

- `VG-PATHLIB-PREDICATES` (18.169, changed): the baseline now holds only the three sites whose path never comes from
  the user (the bundled quickstart, the vendored message catalogues, the folder a conformance run makes for a generated
  corpus), each with a reason under `reasons`, and the check fails on a site with no reason or a reason for no site.
  The other 29 sites go through `signing.is_regular_file`, `is_directory`, `path_exists` or `is_link`, so a path inside
  a folder that cannot be entered gets one answer on Python 3.12, 3.13 and 3.14. Two more seeded faults: a reason
  removed from the baseline, and agentce.toml discovery back to a bare `is_file()`.

## 0.115.0

- `VG-EUAIACT-CLUSTER2` (18.37e, new, tier quick) runs 68 cases through the real Python, TypeScript and Java CLIs.
  Eight eu-ai-act controls shared two rules. DOC-03, OVS-06, ROB-05 and ROB-06 all checked that a consequential call
  came from an enforcement point; INC-04, INT-03, REC-06 and ROB-03 all checked that no ToolCall had a dangling
  reference. Each now tests what its title says. DOC-03 also needs a declared egress-proxy or firewall denominator whose
  count matches the captured calls (the new catalog field `evaluation.requires_denominator`, refused at load as
  `catalog.control.requires_denominator_invalid` when malformed). ROB-05 and ROB-06 are rung 3 and read not_assessed.
  Fifteen seeded faults, five per engine: the requirement ignored, a count above the declared one accepted, a security
  ref of the wrong type counted, the reference closure cut to one hop, and the targetWhere filter dropped so DOC-03 and
  INT-03 judge every ToolCall.
- `VG-FAIL-ON-PARITY` re-pins scenario F10c from 7 matched to 5: on the insufficient-evidence project ROB-05 and
  ROB-06 now read not_assessed, so `outcome=="non-conformant"` no longer matches them.
- The CI `demo-fault` job runs on 40 lanes (was 38): with VG-EUAIACT-CLUSTER2's fifteen faults the heaviest of 38
  shards was estimated at 12.5 minutes, over the 12.0 lane bound; at 40 it is 11.2.

## 0.114.0

- `VG-BYO-CATALOG-WALK` (18.181, changed). The engines dropped a catalog entry that was there but was not a file.
  With --allow-unverified-catalog, a control that was a FIFO, a dangling link or a folder vanished and the run exited
  0, and a FIFO named catalog.yaml hung every engine. A signed catalog still verified after someone moved its control
  or shape file out and linked it back. All three engines now refuse such an entry unopened as `input.catalog_unreadable`, and
  refuse a signed catalog holding a file linked outside it as `catalog.link_outside`. The gate adds eight scenarios
  (19 to 26) and three seeded faults per engine: the walk keeps a non-file entry, a folder named like a control is
  entered, and catalog verification accepts a file linked outside. The Java symlinked-folder fault's search text
  follows the walk's new `else if`; the fault is unchanged. The Java unenterable-folder fault now skips the entries of a
  folder Java cannot enter, as the Python one does, because an entry Java cannot stat is now refused on its own.
  All three engines decide whether a link stays inside the catalog by folder identity, not by how the path is
  spelled. Scenario 27 signs a catalog whose control is an absolute link to a file inside it. Scenario 28 reaches
  that catalog through its path in another letter case, and scenario 29 through the macOS firmlink
  `/System/Volumes/Data`. Both run only on macOS, so CI on Linux skips them, and neither has a seeded fault. The
  gate's demo estimate rises to 1634 seconds and the `demo-fault`
  job gains two lanes (38) so every shard stays under its bound.
- `VG-PATHLIB-PREDICATES` (18.181, changed). Its baseline drops the two `catalog.yaml` `is_file` calls that `catalog lint`
  and `catalog sign` no longer make.

## 0.113.0

- `VG-REPORT-VALIDATE` (18.167, changed). `report --validate` skipped an optional artifact it could not read, as if it
  were absent, so an unreadable auditor.json left the report valid on Python 3.14, TypeScript and Java. All three
  engines now report a file that is there but cannot be read as `cannot read`: no read permission, behind a folder that
  cannot be entered, a FIFO, a dangling link. They also check the data files every run writes and claim.json, and
  answer a failed validation with the key `report.validation_failed`. The gate adds a report whose auditor.json cannot
  be read and expects exit 3, the key and the one problem line in each engine. Its seeded fault reads the file as absent.
- `VG-JSON-INTEGER-LIMIT` (18.167, changed). The runtime_drift.jsonl fault's search text follows the line check's new
  argument name; the fault is unchanged.

## 0.112.0

- `VG-COVERAGE-UNKNOWN` (18.134, added). The engines worked out whether an independent count of an agent's calls
  backed its records and wrote that to coverage.json, but no verdict used it, so an agent with no such count could
  read clean on the integrity and record-keeping checks even if it called a provider around the gateway. All three
  engines now read every INT and REC check insufficient_evidence for a subject whose coverage is unknown (SPEC §6.5)
  and list it under blind-spots.json `coverage_unknown`; a recorded violation stays non-conformant. A denominator
  counts only when it is independent: never one of the subject's own evidence sources, never an admission
  attestation no engine can verify, and never a manifest the bundle manifest does not list. `agentce readiness`
  prints NOT READY and names the unknown coverage. A denominator's own records are left out of the agent's captured
  count only for the types that denominator covers and counts, so a firewall whose listed manifest counts three calls
  while the gateway recorded one reads as a gap, never as covered, and an egress log that covers only tool calls never
  hides its own session records. The gate runs 32 cases through the three real CLIs, with six seeded faults in each
  engine.
- `VG-TRUST-MATRIX` (18.134, changed). The cells fx-INT-01-en-coverage-unknown and fx-INT-04-en-coverage-unknown leave
  the open list: they now read insufficient_evidence in all three engines.
- `VG-QUICK-PATH` (18.134, changed). Step 2's derived profile names no independent denominator, so the INT and REC
  controls now read insufficient_evidence there, beside DOC-01; the step checks each is listed under blind-spots.json
  `coverage_unknown` and every other control still reads not_applicable.
- `VG-ROB02-UNTRUSTED`, `VG-CND05-CHAIN`, `VG-QUARANTINE-NO-PASS`, `VG-DOC01-COMPONENTS`, `VG-EUAIACT-CLUSTER1`, the ten
  gates built by approval_outcome.sh (`VG-APPROVAL-OUTCOME` and the nine that share it), `VG-AUDIENCE-PRESETS` and `VG-SECURITY-VIEW` (18.134, changed). Each fixture subject is
  meant to be fully captured, so its bundle now lists a system-of-record export counting the case's own records and
  its profile names it as the subject's denominator; every pinned verdict and exit code holds.

## 0.111.0

- `VG-CND06-REFUSALS` (18.173, added). CND-06 looked only at instructions that declared an untrusted source. The
  engines skipped an instruction that declared no source, never checked a policy-engine deny with no Refusal, and took
  the agent's word when it alone said an instruction came from a user. All three engines now need a Refusal for
  every instruction from an untrusted or unknown source and for every enforcement-point or independent-system deny of
  a call that acts on an instruction, directly or through the Decision it executes; a Refusal the agent records counts.
  An authorised source the records do not show, and a deny whose request the bundle does not hold, read
  insufficient_evidence unless refused. The gate runs 45 cases through the three real CLIs, with one seeded fault in
  each engine.
- `VG-TRUST-MATRIX` (18.173, changed). The four CND-06 cells 18.173 closes leave the open list, and the quick tier now
  runs the eight CND-06 refusal and deny cells on every pull request.

## 0.110.0

- `VG-INTEGRITY-CAP` (18.132, added). The engines wrote integrity results to integrity.jsonl and counted them, but no verdict
  used them. A control could pass on an enforcement-point record whose stream failed its hash check, had a gap,
  carried no integrity at all or was chained only after export. All three engines now cap the verdict (SPEC §6.6,
  §8.1): a pass that rests on an enforcement-point or independent-system record on a failed, gap, reordered or missing
  chain reads insufficient_evidence; a pass that rests on an enforcement-point record on an export_chained, unsigned or
  unanchored stream reads partial; a timing control (INC) leaves a time_suspect stream's records out. An anchor whose
  time has no UTC offset, or none at all, anchors nothing, and a record time that cannot be placed against an anchor
  reads time_suspect in every engine. A violation never changes. The gate runs 21 cases through the three real CLIs,
  with six seeded faults, two in each engine: the ceiling and the anchor rule.
- `VG-SOURCE-CLASS`, the `approval_outcome.sh` gates, `VG-CND04-PROVENANCE`, `VG-CND05-CHAIN`, `VG-EUAIACT-CLUSTER1`,
  `VG-HUMAN-ACTOR`, `VG-QUARANTINE-NO-PASS` and `VG-ROB02-UNTRUSTED` (18.132, changed). Their fixtures now chain and
  anchor each source's records (`verification/gates/fixture_integrity.py`), so the cases judge what they judged before
  rather than the cap. A case that pins its own integrity keeps it.
- `VG-STREAM-SOURCE-BOUND` (18.132, changed). Its integrity faults point at the code's new place; they seed the same
  defects.

## 0.109.0

- `VG-CND04-PROVENANCE` (18.122, added). CND-04's shape asked only that a consequential action name an instruction,
  so a tool call on tool output, or on a user instruction that records no principal, passed. All three engines now
  follow the instruction's refs.parent and refs.origin chain to its last instruction. A call or consequential decision
  is non-conformant when that chain holds an untrusted Appendix F class or a reference the bundle does not hold, when
  the last instruction records no principal, when the principal's role is not one the task type's
  `authorised_principal_roles` lists, or when the delegation the call rests on records `agentce:chainVerified` false.
  A delegation only the agent reports, no delegation, or a principal with no role reads insufficient_evidence. The
  gate runs 21 cases through the three real CLIs, with verified-principal twins that read conformant, and nine seeded
  faults, three in each engine: the chainVerified check, the principal check and the role check.
  The seeded-fault demos run in 26 lanes, since this demo put one of 25 over the 12-minute lane bound.
- `VG-SOURCE-CLASS`, `VG-HELD-DECISION-EVERYWHERE` and `VG-APPROVAL-OUTCOME` (18.122, changed). Their CND-04 cases now
  carry what the new reading needs: a principal with a role, a task type that authorises it and a verified delegation.
  VG-SOURCE-CLASS adds `probe-k-cnd-04-self-report-unattested` (62 cases), which reads insufficient_evidence.
- `VG-TRUST-MATRIX` (18.122, changed). The matrix adds the CND-04 role cells and the CND-05 cells where an action's
  instruction is absent, null or a model call. CND-05 reads a tool call that names no instruction as
  insufficient_evidence. The 24 CND-04 cells that now agree leave `open.json`.

## 0.108.0

- `VG-CATALOG-READ-ONCE` (18.171, added). `assess --catalog-dir --trust-root` loaded a catalog's files in one read and
  checked its signature over a second read, so a writer who swapped a control file between the two reads got the edited
  control into a report that exited 0 and said the catalog verified. Each engine now reads a catalog's covered files
  once and loads, checks the signature over, and digests those same bytes; Python's `catalog lint`, `catalog sign` and
  packaging take them too. The gate runs the three real CLIs with a writer that changes `controls/DOC-01.yaml` right
  after the engine's first read of it: each engine reads the file once, assesses only the signed bytes, and refuses the
  signed bytes put back after the read as `input.catalog_unverified`. Four seeded faults: the loader in each engine
  reads the control from disk again, and Python's `catalog sign` signs a fresh read instead of the bytes it linted.
- `VG-BYO-CATALOG-WALK` and `VG-BYO-CATALOG-PARITY` (18.171, changed). Their faults searched for loader and
  signature-check lines that the one-read change rewrote; they now search for the new lines and still break the same
  checks.

## 0.107.0

- `VG-TASK-BUDGET` (18.175, added). The domain binding declares each task type's budgets (`task_scopes[].budgets`:
  tokens, compute_seconds, spend), but no engine read them. A spend call where the spend budget is 0, or 5000 tokens
  against a 1000-token budget, never failed CND-07, which read an action over budget only when a `budget_exceeded`
  Refusal named it. All three engines now sum the input and output tokens of each task's ModelCalls against its tokens
  budget, fail a spend call against a spend budget of 0, and fail every action of a task once it is over. A budget the
  records cannot tell reads insufficient_evidence through a new evidence shape: compute time and spend amounts (no
  record carries them), a usage left out, a task with no model call, or a limit the binding writes as a string, a
  negative or a fraction. A ModelCall in a task is now in CND-07's population. Every engine caps token counts at
  2^53. A usage or a sum past 2^53 - 1 reads over every readable limit, and a limit past 2^53 - 1 is unreadable. The
  gate runs 43 cases in the three CLIs, among them a readable overrun beside an unreadable usage, a second subject's
  overrun, an untasked model call, a Refusal naming an event not held, a limit written 1000.0, 513 calls whose sum
  passes 2^63 and a limit past 2^53. Three seeded faults, one per engine, make the token verdict never read over.
- `VG-ENGINE-GOLDENS-FRESH` (18.175, changed). Its Python graph-builder fault searched for the `withinScope` line
  18.175 rewrote; it now edits the new line and still makes every ToolCall and ResourceAccess read within scope.
- `VG-TRUST-MATRIX` (18.175, changed). `fx-CND-07-en-fail` (5000 tokens against a 1000-token budget and a spend call
  against spend 0) now reads non-conformant, its expected word, in all three engines.

## 0.106.0

- `VG-TASK-SCOPE` (18.174, added). The domain binding declares each task type's effect scope (`task_scopes`: the
  tools and effect classes a task's actions may use), but no engine read it: an irreversible call in a task whose
  scope allows read only read CND-01 conformant unless a gateway had also denied it. All three engines now hold a
  ToolCall that names a task, by `refs.task` or `agentcetask`, to every scope declared for that task's type, or for
  the only type when the binding declares one and no entry it cannot read; a task held to no scope, or a call with no
  effect class, is out of scope. A call in no task keeps the deny-only reading. The gate runs 42 cases in the three
  CLIs, among them a second subject, a deny before and after the call and one naming an event not held, an unreadable
  entry (a misspelled key, an unquoted number, a bare string) beside a valid one, and the get-evidence skill's
  task-scope template filled in as a user would, which `lint_profile --domain` must also read cleanly. Three seeded
  faults, one per engine, make the scope ignore the effect class.
- `VG-TRUST-MATRIX` (18.174, changed). `fx-CND-01-en-fail-uea` and `fx-CND-01-en-fail-uep` (an irreversible call in a
  task whose scope allows read only) now read non-conformant in all three engines and leave the open list.
  `fx-CND-01-se-fail-uep` expects `non_conformant`, the word the engines now give, where it expected
  `insufficient_evidence`. The quick tier runs seven CND-01 cells more (74).
- `VG-ENGINE-GOLDENS-FRESH` (18.174, changed). Its graph-builder fault searched for the `withinScope` line 18.174
  rewrote; it now edits the new line and still makes every ToolCall and ResourceAccess read within scope.

## 0.105.0

- `VG-DEVIATIONS-PARITY` (18.186, changed). A deviation register entry is not applied to a control that fails on the
  SPEC §10.4 human-actor rule. S24 (an approval with no `session_ref`) and S25 (an override made in the agent's own
  session) pin CND-02, OVS-01, OVS-07, OVS-08 and RSK-02 as non-conformant with no deviation and one not-applied
  limitation each, while OVS-05's ordinary failure is still waived. S26 runs a third-party catalog whose ToolCall shapes
  reach the decision's approval through a sequence path (`agentce:executes` then `agentce:approvedBy` or
  `agentce:reviewedBy`) or through a nested `sh:node` or `sh:qualifiedValueShape`; those controls stay non-conformant
  too. S27 runs paths that repeat `agentce:derivedFrom` before `agentce:approvedBy`, directly or across a nested
  `sh:node`; those controls stay non-conformant as well. Five new seeded faults: Python's and Java's `apply_deviations`
  ignore the human-actor flag, TypeScript's check looks only at the violation's focus, Java's property reads skip nested
  shapes, and Python's walk takes each distinct step once instead of following it until it reaches nothing new.

## 0.104.0

- `VG-TRUST-MATRIX` (18.176, added). The trust-model case matrix now runs as a build gate. It has 782 cells, covering
  where an instruction came from and who recorded it, its lineage and principal, the gateway twin or PolicyDecision
  that backs it and when each was recorded against the call, memory writes and reads, refusals, and the focus class of
  every catalog control whose `min_source_class` is above `self_report`. Each cell names the SPEC section its expected
  outcome comes from, and `fixtures/trust_matrix/build.py` builds its bundle from the cell's dimension values. The
  quick tier runs 67 cells; `AGENTCE_VERIFY_TIER=full` (which `--full` now hands every gate) runs all of them. Each cell
  runs through the real Python, TypeScript and Java CLIs. The gate fails when a cell reads a pass the SPEC rules out,
  when the engines disagree, or when a cell fails closed where it should pass, unless `fixtures/trust_matrix/open.json`
  lists the cell with its status and the item that closes it. That list can only shrink. A listed cell that reads
  AGREE fails the gate until it is removed. Coverage checks run before any cell and fail on a matrix whose digest is
  not the pinned one, a declared value no cell uses, a catalog control above `self_report` with no cell for one focus
  class, or an open entry with no owner or outside the pinned set. Three seeded faults, one per engine, let any
  record attest an instruction again, which turns the CND-05 cells red.
- `VG-SOURCE-CLASS` (18.176, changed). A gateway twin with no principal no longer backs an instruction that needs one.
  A twin or PolicyDecision recorded after the call it would back no longer backs it either. Both now read
  `insufficient_evidence`.
- `./verification/run --quick` and `--full` now set `AGENTCE_VERIFY_TIER` to `quick` or `full` for each gate they
  run; `--gate` and `--demo-fault` leave the caller's environment as it is.

## 0.103.0

- `VG-SOURCE-CLASS` (18.127, added). A control declares the least source class its verdict may rest on
  (`min_source_class`), but no engine applied it: `source_class_satisfied` was always true, so an agent's own log
  saying an instruction came from the user, plus one gateway record of the tool call, read CND-04, CND-05 and ROB-02
  conformant. All three engines now hold every assertion to its control's minimum after the shape verdict: a focus
  whose judged class is below it reads `insufficient_evidence`. For CND-04 and CND-05 the judged class includes the
  acted-on instruction's (`evaluation.source_class_from`), which counts as enforcement-point evidence only when an
  enforcement-point record attests it: its own class, an enforcement-point twin with the same content, task, class and
  principal, or an enforcement-point PolicyDecision allowing the acting call for the instruction's principal. The gate
  runs 44 cases: the audit probes A to J, each with an attested twin that stays conformant, eleven hostile
  corroborations, each one field away from a conformant case, and three consequential decisions that name no
  instruction (judged as the agent's own record unless the enforcement-point call executing it acts on an attested
  instruction). It also checks that a `min_source_class` that is not a source class (seven values, among them `any`, a
  missing member and `Enforcement_Point`) is refused at catalog load with `catalog.control.min_source_class_unknown`
  and exit 3 in every engine, and a `source_class_from` that is not a list of known links (five values) with
  `catalog.control.source_class_from_unknown`. Three seeded faults, one per engine,
  make the judged-class comparison always pass.
- `VG-CND05-CHAIN` (18.127, changed) now runs 53 cases. A self_report user instruction and a self_report
  memory_trusted one with no origin, each acted on by an enforcement-point call, read `insufficient_evidence`; an
  enforcement-point ingress twin and an enforcement-point memory_trusted instruction read conformant.
- `VG-ROB02-UNTRUSTED` (18.127, changed) now runs 51 cases. A decision acting on an instruction that declares no
  `source_class` is non-conformant, as one declaring `tool_output` is: leaving the member out never reads better.
  `decision-used-nothing` (a consequential decision that used nothing recorded) now reads `insufficient_evidence`.
- `VG-HELD-DECISION-EVERYWHERE` (18.127, changed). In `cnd04-bareid-toolcall-acts-on-instruction` the instruction the
  call acts on was an independent_system record, which does not attest an instruction for CND-04 (only an
  enforcement-point record does), so the case read `insufficient_evidence`. The instruction is now an enforcement
  point's record and the case still reads conformant: it checks the call's decision scope, not the class.
- Gate fixture catalogs whose controls declared `min_source_class: any` (under `fixtures/`: audience_presets,
  auditor_view, blind_spots, buyer_view, golden_loop, project_view, project_view_deviations, quick_path,
  records_exit_code and self_approval) declare `self_report`, the lowest minimum, since `any` is not a source class
  and is now refused at load. No gate outcome changes (18.127).

## 0.102.0

- `VG-DEMO-SHARD-COVERAGE` (18.71e, changed). The demo lanes were dealt one whole gate at a time, so the heaviest
  gate (up to 14 minutes) set the floor on a lane against the 20-minute job timeout. `./verification/run
  --demo-fault <ID> --fault <N>` now runs only the named faults, and `shard.py` deals (gate, fault) pairs: a gate over
  `max_chunk_seconds` is cut into runs of consecutive faults, the chunks go longest first to the lightest shard, and
  `--dry-run` prints one `<gate> <fault>` line per pair. `--balance-check` also fails a shard over
  `lane_bound_minutes`. The gate now checks that every registered fault lands in exactly one shard, both in the dealt
  pairs and in the arguments handed to the runner, and that the runner runs only the selected faults and refuses
  options a command would ignore, a command option given twice (`suite.repeated_option`) and an empty `--root` or
  `--registry` (`suite.empty_value`). `--dry-run` prints the same slice the run hands the runner. While a demo has a
  fault applied, a runner the gate calls skips the search-text check for that gate's faults in the registry lint, so a
  gate that calls the runner turns RED for what it checks, not for the lint. Five new seeded faults: a pair dealt twice, a pair left off the
  runner's command line, the runner ignoring `--fault`, the lint refusing the applied fault, and a repeated option
  accepted.

## 0.101.0

- `VG-DEMO-SHARD-COVERAGE` (18.71b, changed). The demo-fault shards were dealt round-robin, so at 18 lanes one shard
  carried 20 minutes of demos and hit the job timeout while others took 5. `shard.py` now gives the gates to the
  shards longest first, each to the lightest shard, from the seconds in the new `demo-durations.json` (the longest of three CI runs, at 24 lanes;
  an untimed gate counts as `default_seconds`), and `shard.py --balance-check <total>` prints each shard's estimate.
  The gate now checks that the balanced split places every gate once, that the balance check passes at the
  workflow's lane count, and that `shard.py --dry-run` prints the balanced slice for every lane. Four new seeded
  faults: a dropped gate, round-robin dealing, an untimed gate not reported, and a demo path that ignores the
  balanced split.
- `VG-BYO-CATALOG-PARITY` (18.71b, changed) and `VG-BYO-CATALOG-WALK` (18.71b, added). One gate's demo ran 16
  minutes on its own, too long for one demo shard. The gate now runs scenarios 1 to 11, on signatures, catalog
  identity and the trust root, with those six seeded faults. The new `VG-BYO-CATALOG-WALK` runs scenarios 12 to 18,
  on what the catalog walk and the loaders read, with the other twelve. Same script, same checks: one argument picks
  the half.

## 0.100.0

- `VG-CND05-CHAIN` (18.124, changed) now runs 49 cases. SPEC §6.2.3 says a tool call's or a decision's
  `refs.instruction` names an Instruction. When it named a held event of another type (a ModelCall, a SessionStart, a
  MemoryWrite) or a trusted memory record or model output, CND-05 read conformant in all three engines, though no
  record showed who gave the instruction. Each engine now passes an action only when its ref names a held
  Instruction whose chain is trusted. The new cases cover those five targets, an instruction the bundle does not hold,
  and a bare event id, for tool calls and decisions. Each new case also holds an action on a user instruction that
  must not fail, and a twin holds those other events with nothing acting on them. Three new seeded faults, one per
  engine, let any held event or record count as an instruction again.

## 0.99.0

- `VG-BYO-CATALOG-PARITY` (18.168, changed) now runs 18 scenarios. A catalog's signature left out dot-named files
  and anything under `__pycache__`, while the loaders read a dot-named control file and whatever file a control
  named as its shape. So a control could be added, or a shape edited, after signing, and
  `assess --catalog-dir --trust-root` accepted the catalog in all three engines. The loaders now read only the
  files the signature covers, by their exact names: a dot-named control file is never read, and a shape that names
  any other file is refused as `catalog.shape.unresolved`. The new scenarios add a dot-named control file to a
  signed catalog, and name DOC-01's shape as a dot-named file and through `__PYCACHE__`. Six new seeded faults,
  two per engine, make the loader list the controls folder itself or skip the shape check.

## 0.98.0

- `VG-BYO-CATALOG-PARITY` (18.164, changed) now runs 15 scenarios. A catalog's signature left out the files behind
  a symlinked folder, while the loader read them. So a catalog signed with `controls/` linked could be edited behind
  the link after signing, and `assess --catalog-dir --trust-root` accepted it in all three engines and printed the
  edited text. Each engine now walks into a symlinked folder the way the loader reads it, so the signature covers
  those files. A folder reached a second time, such as a link back to an ancestor, is refused as
  `input.catalog_unreadable`, signed or not. The new scenarios sign a catalog with `controls/` linked, edit it
  behind the link, and add a link to an ancestor folder. Three new seeded faults, one per engine, stop the walk
  from entering a symlinked folder.

## 0.97.0

- `VG-PATHLIB-PREDICATES` (18.163, new, quick tier) lists every bare `Path.is_file()`, `is_dir()`, `exists()` and
  `is_symlink()` call in the Python engine and fails on one that is not in a committed baseline, which only shrinks.
  Those four answer a path inside a folder that can be listed but not entered differently by Python version: 3.12
  and 3.13 raise, 3.14 says the path is not there. So `validate --bundle`, `assess --profile` and `sign` crashed
  with `internal.unexpected` on 3.12 and 3.13 for such a path, where 3.14, TypeScript and Java give the ordinary
  refusal. Those three sites now go through the engine's own path helpers, which answer the same on every Python.
  Its seeded faults put the bare call back in two of them.

## 0.96.0

- `VG-PYTHON-VERSIONS` (18.114, new, full tier) runs the Python engine's whole test suite on 3.14, 3.13, 3.12.10 and
  3.12.3 at the same time, each in its own copy of the working tree and its own isolated environment. The engine accepts Python 3.12 and newer,
  but CI tested only 3.12.3, and newer versions gave different answers. On 3.13 and 3.14 the engine read a manifest
  member that is a symlink loop and failed its hash check, where it should refuse it as an unsafe path. On 3.12.10,
  3.13 and 3.14 `agentce conformance -- run` ran the suite, where the other engines refuse it. On 3.14 the engine
  called an unreadable release folder "not a release bundle". On every version, a coverage denominator manifest whose
  root is a list crashed the engine; it now counts the source's own events, as TypeScript and Java do. The engine now
  refuses a manifest nested past 1000 levels with `input.coverage_denominator_manifest_invalid` on every version. CI
  runs the gate as one job per version. Its seeded fault removes the symlink-loop check.

## 0.95.0

- `VG-APPROVAL-REFS-RESOLVED` (18.158, changed) now covers interrupts whose decision name points at nothing. Such an
  interrupt counted as a human's even when the human logged in through the halted tool call's own agent, while the same
  interrupt naming the tool call correctly did not. Each engine now refuses that login when any held record other than
  the login itself concerns its agent. 26 OVS-07 cases (66 in all) cover other letter case, misspellings, unheld ids,
  null, empty and padded names, and twins through an identity provider that still read conformant. Three new seeded
  faults, one per engine, drop the check.

## 0.94.0

- `VG-FAIL-ON-PARITY` (18.150, changed) no longer accepts a `--fail-on` value that the report never uses. A typo such as
  `outcome=="non_conformant"`, `severity=="critical"` or `mode=="auto"` matched nothing, so the run passed with exit 0
  even when a control was non-conformant, and the gate's own F2 and F7 pinned that as correct. Each engine now refuses
  an `outcome`, `severity` or `mode` value outside its schema values with `input.fail_on_invalid_expression`, exit 3,
  and writes nothing; a syntax error or an unknown field is still reported first, and `control`, `subject`, `family`
  and `rung` stay free text. F2 and F7 are now refusals, and eight scenarios are added: an outcome typo, a mode typo,
  an empty outcome, a case variant in a later OR group, a syntax error and an unknown field before a bad value, and an
  escaped and a hyphenated closed value that are still accepted (46 CLI scenarios). The parser corpus keeps its 6,000
  fuzzed expressions byte for byte and adds 500 more from the same seed whose outcome, severity and mode comparisons
  mostly use their own values, sometimes a near miss, so the evaluator is still fuzzed now that random values for
  those fields are refused (6,559 parser expressions). Three seeded faults are added, one per engine, each removing
  only that engine's enum check.
- `VG-RECORDS-EXIT-CODE` (18.150, changed): G7, an opt-in that matches nothing, now uses `control=="NONE-00"`, since
  its old `severity=="critical"` is refused.

## 0.93.0

- `VG-HELD-DECISION-EVERYWHERE` (18.156, new, tier quick) checks that every glue read of `refs.decision` names the
  held decision the approval bound names since 18.148, in all three engines. The `executes`, `resultedIn`,
  `overriddenBy`, `interruptedBy` and `notifiedBy` edges, `executesConsequential` and OVS-07's Override check compared
  the exact `refs.decision` text, so a ToolCall naming its consequential decision as `d1` dropped out of CND-04 and
  OVS-03 (not applicable where `agentce:event/d1` read non-conformant), and a bare-id Override, Notice or adverse
  Outcome read as linked to no decision. Each read now resolves the name the same way: the name itself, else trimmed of
  the human-actor rule's id padding, else `agentce:event/<trimmed name>`, checked against the held Decisions only,
  which each engine now knows before it reads the first record. A name for no held Decision links none, as before.
  The gate holds 32 cases and nine seeded faults: in each engine, the glue edges and `executesConsequential` reading
  the raw text again, and the Override check resolving against every held event.

## 0.92.0

- `VG-APPROVAL-REFS-RESOLVED` (18.157, changed) now also covers a `refs.decision` that is present and null. Each engine
  skipped a ToolCall or Outcome with `"decision": null`, so an approve logged after it still gated the decision and
  CND-02 and OVS-08 read conformant. The same records naming `d1` or an unknown id read non-conformant. A null now names no
  held decision, so its time bounds the approvals of every held decision, as an unknown name has since 18.148; it
  withdraws none. A ToolCall or Outcome with no `refs.decision` key is still no decision's action. The gate grows from
  31 to 40 cases (four null-effect cases that read conformant before the fix, a later null ToolCall that bounds nothing,
  a reject after a null ToolCall, a null on the approval, and two cases with no decision reference) and from ten to
  thirteen seeded faults: each engine skipping a null again.

## 0.91.0

- `VG-APPROVAL-REFS-RESOLVED` (18.148, new, tier quick) checks that the approval bound reads every `refs.decision` as
  the held decision it names, in all three engines. CND-02 and OVS-08 grouped the ToolCalls, Outcomes and approvals of
  a decision by the exact `refs.decision` text, so an Outcome naming the decision as `d1` or `agentce:event/d1 `, while
  the approval named `agentce:event/d1`, bounded nothing, and an approve logged hours after the decision took effect
  read conformant. A reject written as `d1` did not withdraw the same human's approve either. Each engine now
  resolves a `refs.decision` to a held decision: the name itself, else the name trimmed of the human-actor rule's id
  padding, else `agentce:event/<trimmed name>`. An approval naming no held decision approves and reviews none. A
  ToolCall or Outcome naming no held decision (another letter case, a `urn:`, another event's id, an empty string) may
  be any decision's effect, so its time bounds every decision's approvals, though it withdraws none. The gate holds 31
  cases and ten seeded faults: raw grouping in all three engines, an unresolved effect bounding nothing in all three,
  a bare id resolved against any held event in Python, and, in all three, a human-actor check that counts only the
  resolved decision's agents, so an Interrupt naming a ToolCall (by IRI, bare id or padded) would accept a login from
  that ToolCall's own agent.

## 0.90.0

- `VG-STREAM-SOURCE-BOUND` (18.151, changed) also checks that a record's claimed integrity stream never joins it to
  another source's hash chain, in all three engines. Integrity verification grouped chains by the stream a record
  names in `data.integrity.stream`, so an agent's own record naming the gateway's chain, with a hash that matches
  nothing, turned the gateway's chain from `verified_weak` to `failed`. Chains are now keyed on the record's source and
  the stream it claims, and results are ordered by stream, then source. The manifest's anchors and a `sig_ref` name no
  source, so when two sources claim one stream id, neither chain under it reads `verified`: it reads `verified_weak`
  instead, and an agent's own chain can't borrow the gateway's anchors. The gate gains six cases, each pinning every
  integrity stream and status `integrity.jsonl` holds. In two, the agent's bad-hash record sits between the gateway's
  two chained ToolCalls and claims their stream, against a twin where it claims nothing: the gateway's chain reads
  `verified_weak` in both, and the hijacker gets its own `failed` entry. In four, the gateway's chain is
  `export_anchored` with the manifest's anchor, against the agent's own well-formed anchored link claiming that id:
  alone the gateway reads `verified`, shared neither chain does, and an anchor earlier than the records marks every
  chain `time_suspect`. The gate gains two seeded faults per engine: one keys the chain on the claimed stream alone, and
  one lets a shared id read `verified`.

## 0.89.0

- `VG-TIME-ORDER-INSTANT` (18.145, new, tier quick) checks that ingest compares event times as instants when it checks
  time order, in all three engines. Ingest compared a record's time with the previous one in its stream as text, so a
  time written with a UTC offset or a fraction of a second could sort before an earlier one. A human's reject at
  00:30 two hours behind UTC (02:30Z) after their approve at 01:00Z was quarantined for `time_order`, and CND-02 read
  `insufficient_evidence` instead of catching the withdrawn approval. The other way round, a record truly earlier
  than the one before it but written with an offset was accepted. Ingest now applies the offset and compares fractions
  exactly, and keeps text order only for two times that cannot be compared as instants (one with no offset). The gate
  runs 11 cases through the real Python, TypeScript and Java CLIs: a reject written with an offset, as the same
  instant with another offset, and with a fraction of a second, and the enforcement point's action written with an
  offset, each against its twin written in UTC; a truly earlier reject written with an offset and in UTC; and a reject
  with no offset. The gate has one seeded fault per engine that compares the times as text.

## 0.88.0

- `VG-STREAM-SOURCE-BOUND` (18.147, new, tier quick) checks that a record's claimed integrity stream never joins it to
  another source's time-order group, in all three engines. Ingest checked time order per stream and took the stream
  from the record's own `data.integrity.stream`. An agent's own record could name the enforcement point's stream with a
  later time, and ingest then quarantined the enforcement point's genuine ToolCall for `time_order`, so CND-02 read
  `insufficient_evidence` instead of catching the violation. Ingest now checks time order per source and claimed
  stream. The gate runs 12 cases through the real Python, TypeScript and Java CLIs. Eight have an agent record claim
  another source's stream: from a self_report source, an undeclared source and an independent_system source, naming
  the gateway's own stream id, arriving after the ToolCall, naming another subject, and claiming the identity
  provider's stream before an approve and before a reject. Each reads exactly like its twin, and a twelfth case shows
  one source still keeps time order within its own stream. The gate has one seeded fault per engine that keys time
  order on the claimed stream alone.

## 0.87.0

- `VG-QUARANTINE-NO-PASS` (18.149, changed) checks the exit code a late record gives. When a guard's untrusted write
  of `mem:r1` arrives in order, ROB-02 is non-conformant and `assess` exits 1. When the same write arrives late, ingest
  quarantines it for `time_order` and ROB-02 reads `insufficient_evidence`. That exited 0, because ROB-02 is not
  severity high, so a CI job on the default exit code passed when the record arrived late. Now the engine judges the
  readable quarantined records with the held ones, also when the held records alone lack the control's minimum
  evidence. When that reads non-conformant, `assess` exits 1 and the `--json` output lists the control under
  `withheld_non_conformant` with the message key `withheld_reads_non_conformant`. A `--fail-on` expression matches it
  as the non-conformant outcome it would be on time. The outcome stays `insufficient_evidence` and names the late
  record. A quarantine that changes nothing keeps today's exit code, and so does a record ingest could not read. The
  gate grows to 25 cases. Two run with `--fail-on 'outcome=="non-conformant"'`, two put an unreadable line beside the
  write, and two deliver the guard's read late when it supplies ROB-02's minimum evidence. Every case checks the
  `withheld_non_conformant` listing and `exit_codes`. The gate gains eight seeded faults. In each engine, one makes the
  exit rule ignore the withheld outcome and one compares withheld records only for a conformant or not_applicable
  outcome. In Python, one stops `--fail-on` matching the withheld outcome and one skips the comparison beside an
  unreadable record.

## 0.86.0

- `VG-QUARANTINE-NO-PASS` (18.119, new, tier quick) checks that quarantine never gives a better outcome, in all three
  engines. A record ingest quarantined dropped out of the assessment, so the quarantine itself could pass a control:
  an enforcement-point guard's late ruling that marked `mem:r1` untrusted, quarantined for `time_order` because real
  exports arrive out of order (SPEC §13), left ROB-02 conformant; a schema-invalid Override or Interrupt left OVS-07
  `not_applicable`; a ToolCall whose `refs.instruction` is an array or an `{"@id": ...}` object left CND-05
  `not_applicable`; and CND-02 read conformant when the action's record was quarantined for `time_order` or the same
  human's reject was quarantined (spelled `Reject`, or sharing the approve's id). Now ingest keeps the quarantined
  records that may hold evidence: the ones it could read but refused for where they sit (`time_order`, a
  `duplicate_id` that is not an identical redelivery, `class_mismatch`, `unknown_source`) and the ones it could not
  read (`schema_invalid`, `oversize`). A control that reads conformant, or `not_applicable` for want of a population,
  is judged again with the subject's readable quarantined records; when the outcome changes, or a record ingest could
  not read may belong to the subject, it reads `insufficient_evidence` and its evidence names the quarantined records.
  A quarantine that changes nothing leaves the outcome as it is. The gate runs 18 cases through the real Python,
  TypeScript and Java CLIs, each failing case beside a benign twin, and checks that each `insufficient_evidence` case
  names the quarantined events in its evidence. Seven seeded faults: in each engine a `time_order` record left out and
  the unreadable records ignored, and in Python the evidence left empty. `VG-APPROVAL-ORDER` gains one case and `VG-APPROVAL-WITHDRAWAL`
  two, each a quarantine that now reads `insufficient_evidence`. In `VG-DUPLICATE-ID-CLASS` the agent's own Decision
  that reuses the identity provider's login id still never displaces the login, but that Decision has no approval, so
  CND-02 and OVS-08 there read `insufficient_evidence` instead of conformant.

## 0.85.0

- `VG-APPROVAL-OUTCOME-BOUND` (18.144, new, tier quick) checks that an approval gates a decision only when it is
  recorded before the decision took effect, in all three engines. Every engine bounded an approval by the `ToolCall`s
  that execute the decision only, so a decision with an `Outcome` on record and no `ToolCall`, approved hours after that
  `Outcome`, still read conformant on CND-02 ("High-effect actions are gated by a recorded approval") and on OVS-08.
  Now the approve must be at or before every `ToolCall` that executes the decision and every `Outcome` that names it,
  compared as instants, never as text; an `Outcome` listed after the approval in the file still counts by its time.
  OVS-01 still counts a late approval as a review, and a `reject` still withdraws an `approve` against the `ToolCall`s
  only, as in 0.83.0. The gate runs 20 cases through the real Python, TypeScript and Java CLIs with the
  `VG-APPROVAL-OUTCOME` runner. Six seeded faults, two per engine: the `Outcome`s left out of the bound, and the times
  compared as text. `VG-APPROVAL-OUTCOME`'s TypeScript fault now names the new bound.

## 0.84.0

- `VG-DUPLICATE-ID-CLASS` (18.143, new, tier quick) checks that a record of a lower source class never displaces a
  record of a higher class with the same event id, in all three engines. Ingest used to keep the first record it read
  for each id, whatever its class. An agent could write its own `self_report` record with the id of the enforcement
  point's `ToolCall`, read first, and get the `ToolCall` quarantined, so CND-02 ("High-effect actions are gated by a
  recorded approval") went from non-conformant to `insufficient_evidence`; the same trick dropped an identity
  provider's login. Now the id belongs to the highest class ingest trusts (`independent_system` above
  `enforcement_point` above `self_report`, with an undeclared source's records counted as `self_report`), and the
  ingest quarantines the lower record as `duplicate_id` wherever it sits in the file. Of two records of one class with
  one id, ingest still keeps the first. The gate runs 14 cases through the real Python, TypeScript and Java CLIs with the `VG-APPROVAL-OUTCOME`
  runner, each shadow case against a twin without the shadow. Six seeded faults, two per engine: the class ignored
  at the id check, and the rank read from the class a record asserts instead of the class ingest trusts.

## 0.83.0

- `VG-APPROVAL-WITHDRAWAL` (18.142, new, tier quick) checks that each human's own most recent decision on a decision
  governs whether that human approved it, in all three engines. Every engine used to count any verified `approve` a
  human ever recorded, so one human who approved and then rejected the same decision before the action ran still read
  conformant on CND-02 ("High-effect actions are gated by a recorded approval") and on OVS-08. Now a `reject` or an
  `edit` withdraws every earlier `approve` by the same human, as does one at the same instant or at a time that
  cannot be placed against the `approve`; a later `approve` after that human's `reject` still gates. A decision
  recorded after every `ToolCall` that executes the decision withdraws nothing, since the action already ran under
  the approval; one between two actions withdraws it, since the second ran without it. Times compare as instants,
  never as text. Two distinct humans who each decide once are unaffected: one `approve` and another human's `reject`
  stay conformant on CND-02 outside dual control, and OVS-08 under dual control still needs two approvals. The gate
  runs 28 cases through the real Python, TypeScript and Java CLIs with the `VG-APPROVAL-OUTCOME` runner. A human's
  own `reject` withdraws that human's `approve` even when the `reject` itself fails the SPEC §10.4 human-actor rule
  (no login, another human's login, a padded id), so the check fails closed. Nine seeded faults, three per engine:
  the withdrawal ignored, one human's decisions ordered as text, and a `reject` that fails §10.4 dropped.
- `VG-APPROVAL-OUTCOME`'s six faults and `VG-EUAIACT-CLUSTER1`'s Java distinct-approver fault are re-pointed at the restructured approval code (same faults:
  every outcome recorded as an approve, every outcome counted toward OVS-08's approvers, approvers counted per approval).

## 0.82.0

- `VG-APPROVAL-ORDER` (18.141, new, tier quick) checks that an approval gates an action only when it was recorded at
  or before the action, in all three engines. Every engine used to count a verified `approve` whenever it was
  recorded, so an approval logged hours after the enforcement point had already run the action read conformant on
  CND-02 ("High-effect actions are gated by a recorded approval") and on OVS-08. Now an `approve` adds
  `agentce:approvedBy`, and counts toward OVS-08's distinct approvers, only when its time is at or before the time
  of every `ToolCall` that executes the decision. Times compare as instants through the same literal comparison the
  shapes use (an offset or a fraction of a second is honoured, text order never decides), and a time that cannot be
  compared with the action's gates nothing. A decision no `ToolCall` executes is unaffected, and OVS-01 and RSK-02
  still count a late approval as a review. The gate runs 21 cases through the real Python, TypeScript and Java CLIs
  with the same runner as `VG-APPROVAL-OUTCOME`. Six seeded faults, two per engine: the order ignored, and the times
  compared as text.
- `VG-APPROVAL-OUTCOME`'s runner takes a fixture name (default `approval_outcome`), and its four TypeScript and Java
  faults and two Python faults follow the order test now sitting beside the outcome test; each fault is unchanged.

## 0.81.0

- `VG-APPROVAL-OUTCOME` (18.136, new, tier quick) checks that an approval gates a decision only when its outcome is
  `approve`, in all three engines. Every engine used to count any verified `ApprovalDecided` as an approval, so a
  reviewer's reject, an edit or a record with no outcome read conformant on CND-02 ("High-effect actions are gated by
  a recorded approval") and on OVS-08, even when the action ran anyway. Now only an `approve` adds the new
  `agentce:approvedBy` edge, which CND-02 and nist-ai-rmf OVS-08 ask for, and only an `approve` counts toward the
  distinct approvers that eu-ai-act and baseline OVS-08 count; `agentce:reviewedBy` still takes any outcome, so
  OVS-01, INC-03 and RSK-02 keep counting a reject as a review. The gate runs 25 cases through the real Python,
  TypeScript and Java CLIs. Six seeded faults, two per engine: `approvedBy` written for any outcome, and any outcome
  counted toward OVS-08's approvers.
- `VG-EUAIACT-CLUSTER1`'s three dual-control faults follow the rename of the per-decision set they edit (reviewers to
  approvers); each fault is unchanged.

## 0.80.0

- `VG-HUMAN-ACTOR` (18.140, changed) also checks that the identity-provider login an override, interrupt or approval
  cites names that record's own actor. After 18.126 any held login from an independent or enforcement-point stream
  counted, so a self-reported override by one human could cite another human's genuine login, or a sub-agent's own
  session under the same subject, and OVS-01, OVS-07 and CND-02 read it as the first human's review. A
  `SessionStart` now carries an optional `principal`, the human the identity provider authenticated, and the rule
  fails closed when the cited login names nobody, a service, a blank id or someone other than the actor. Principal
  ids compare after trimming Unicode White_Space and U+FEFF from both ends in every engine, so a trailing space or a
  U+00A0, U+0085 or U+FEFF pad neither splits one human in two nor escapes the delegation-chain check. The rule
  reads the principal any record names, so the login's type check is what stops a gateway's policy decision or
  instruction that names the actor; the three faults that drop that check now fail on those cases. The gate now runs
  88 cases. Six new seeded faults, two per engine: the login-principal check dropped, and ids trimmed with the
  language's own whitespace set. The old OVS-08 fault keyed distinct humans by actor id alone. Since a counted
  actor's login now names that actor, that fault no longer changes any outcome, so it now compares ids untrimmed and
  lets a padded owner id escape the delegation-chain check. The gate now splits the Python cases over three workers
  beside one each for TypeScript and Java, since Python's CLI starts slowest; that keeps the seeded-fault demo, 24
  faults over 88 cases, inside its CI lane's 20 minutes.

## 0.79.0

- `VG-HUMAN-ACTOR` (18.126, new, tier quick) checks the SPEC §10.4 human-actor rule in all three engines. An
  approval, override or interrupt used to count as a human's whenever its actor said `kind: human` with an id, so an
  agent could write its own approval and pass OVS-01, OVS-07, OVS-08, CND-02, INC-03 and RSK-02, the human the agent
  acts for could count as its independent reviewer, and two spellings of one id on one login counted as two humans
  under dual control. Now the actor's `session_ref` must name a held `SessionStart` of someone other than the agents
  the record concerns, from an `independent_system` or `enforcement_point` stream, and the actor must be outside the
  activity's delegation chain; dual control counts approvals that share a login or an id as one human. Override and
  Interrupt gain the optional `session_ref` the rule needs (the schema used to quarantine it). The gate runs 53 cases
  through the real Python, TypeScript and Java CLIs. Eighteen seeded faults, six per engine: the session check
  dropped, any held record taken as the login, the agent's own session accepted, the delegation-chain check dropped,
  dual control keyed by actor id alone, and the concerned agents read from the optional `agent` field without the
  event's `subject`.

## 0.78.0

- `VG-ENGINE-GOLDENS-FRESH` (18.37d, new, tier quick) checks that every TypeScript and Java parity golden is what
  the Python engine writes today. The ports' byte-identical tests compare against goldens captured from Python, and
  nothing used to capture them again, so a change made only in Python left those tests green on an old snapshot.
  18.37 found two graph-builder gaps that way. Each engine's `generate_goldens.py` now builds every golden its tests
  read from one map, five of them through shared producers in `engines/python/tests/parity_goldens.py`, and
  `--check` compares the map with the committed files byte for byte, writes nothing, and names each stale, missing
  or unproduced golden. Any other argument exits 2. The gate's self-test runs `--check` on a copy with one golden
  edited, one deleted and one stray, then the real run checks both engines. The state golden was stale: Python's
  `state.json` has carried `last_outcomes` since 2026-09-24, and TypeScript and Java now load and save it. Three
  seeded faults: a graph-builder change and a `state.json` change made only in Python, and a `--check` that stops
  comparing bytes.

## 0.77.0

- `VG-DEMO-SHARD-COVERAGE` (18.131) now covers the quick tier too. One `build` job used to run all 61 quick-tier
  gates in a row under a 30-minute limit, and on 893cf53 it took 1623 s. The quick tier now runs in a 12-lane
  `quick-tier` matrix: each lane runs `verification/shard.py --quick <index> <total>`, which takes the registry's
  quick-tier gates in order, keeps its own slice, and runs `./verification/run --gate` on each, so a lane judges a
  gate exactly as `./verification/run --quick` does. `build` no longer runs a whole tier, and `quick` fails unless
  `build`, every quick-tier lane and every demo-fault lane succeeded. The gate checks the new job's steps and
  wiring and that the quick-tier partition gives every quick-tier gate to exactly one lane at two totals. Its
  self-test runs `quick_ids()` and `run_slice()` on a made-up registry. Five seeded faults added, nine in all.
  `./verification/run --quick` is unchanged and still runs the whole tier locally.

## 0.76.0

- `VG-EUAIACT-CLUSTER1` (18.37c, new, tier quick) runs 44 cases through the real Python, TypeScript and Java CLIs.
  Eight eu-ai-act controls (DAT-03, INC-03, OVS-01, OVS-07, OVS-08, ROB-07, RSK-02, RSK-03) shared one rule, that a
  consequential decision is reviewed, so most of them passed on records that said nothing about their own
  requirement. Each now tests what its title says. INC-03 checks the decisions an incident names, and every decision
  when any name leads to no held decision. OVS-07 checks that overrides and interrupts take effect and come from a
  human. OVS-08 counts distinct human reviewers, two under dual_control. ROB-07 looks for a response after detection.
  RSK-02 needs a review, or an authorization that names a policy decision the bundle holds. OVS-01 needs a review.
  DAT-03 and RSK-03 are rung 3 and read not_assessed.
  The gate has seven seeded faults. Each engine's graph builder gets two, one that skips an unresolved incident name
  and one that counts reviewers per approval. Python's also gets one that accepts an RSK-02 authorization whatever it
  names. The cases run all three engines and each engine's unit tests cover that check, so one copy is enough. The
  three engines run side by side and the first wrong case stops them, so a seeded-fault run turns red without
  assessing every case.

## 0.75.0

- `VG-CND05-CHAIN` (18.123) adds six cases (37 in all) and six seeded faults, two in each engine's graph builder.
  CND-05 counted an instruction as trusted when its records did not establish a trusted class: one with no
  `source_class`, and a `memory_trusted` one whose origin is a memory read the guard never ruled on (a self-report
  read with no `trust_min`). Both read conformant. Each engine now fails such a chain closed: an instruction that
  declares no trusted source class, and content ROB-02 already reads as never ruled on, mark the chain untrusted. A
  tool call or decision acting on one, directly or through a parent, reads non-conformant; the guard's read at
  `trust_min` trusted stays conformant, and `VG-ROB02-UNTRUSTED` is unchanged.

## 0.74.0

- `VG-CND05-CHAIN` (18.121) adds eight decision cases (31 in all) and three seeded faults, one in each engine's shape
  parser. CND-05 says no tool call or decision acts on an untrusted instruction (SPEC §7.7.4), but its shape targeted
  tool calls only, and each engine read a single `sh:targetClass` per shape, so a decision acting on a `tool_output`
  instruction read conformant. Each engine now reads every `sh:targetClass` of a shape as a union, as SHACL does, and
  CND-05 targets `agentce:ToolCall` and `agentce:Decision`. A decision on an untrusted instruction, typed or not,
  direct or derived, reads non-conformant and names the decision; records with no tool call read
  insufficient_evidence, never conformant; trusted twins stay conformant.

## 0.73.0

- `VG-CND05-CHAIN` (18.37l) is new, with 23 cases and three seeded faults, one in each engine's graph builder. CND-05
  fails an action on an instruction whose chain passes through an untrusted source class (SPEC §7.7.4), but every
  engine read only the acted-on instruction's own class, so a user instruction derived from a tool_output
  instruction passed. Each engine now follows the instruction's `refs.parent` and `refs.origin` lineage for as many
  hops as the records show: the chain is untrusted at an instruction of an untrusted Appendix F class, at a tool call
  or resource access that produced the content, at a memory read the guard marked untrusted, and at a parent or
  origin the bundle does not hold, including one named by a bare event id. Trusted twins,
  parent cycles, an untrusted child of the acted-on instruction and an untrusted instruction off the chain keep it
  passing.

## 0.72.0

- `VG-ROB02-UNTRUSTED` (18.37k) grows to 50 cases and six seeded faults, two in each engine's graph builder. ROB-02
  read conformant on a decision the memory guard never ruled on, as long as the bundle held one enforcement-point
  memory read somewhere: a decision whose own reads only the agent reported, an input record no enforcement-point
  read or write covers, or an input the bundle does not hold. Each engine now marks, per decision, whether the
  guard ruled on all the content it used, and ROB-02 reads insufficient_evidence for a decision it did not. A write
  with no trust and no verdict whose `provenance_origin_class` is an untrusted Appendix F class now counts as
  untrusted. The case file adds those decisions, each beside an enforcement-point read elsewhere in the bundle, with
  guarded and trusted twins and a tainted decision beside an unruled one. The gate also checks that a catalog
  whose declared evidence shape has no target, is declared empty or points outside the catalog folder is refused
  with `catalog.evidence_shape.unresolved` in all three engines, and that Python's `catalog lint` refuses it too.
  An enforcement-point memory read now counts as the guard's ruling on its records only when it carries a
  `trust_min`; a read that did not filter by trust leaves a record the guard never ruled on. Two cases span two
  subjects: an untrusted write under one subject, used by the other subject's decision as an input or through its
  own enforcement-point read, reads insufficient_evidence for the deciding subject, and the gate reads that
  subject's ROB-02 assertion. The new faults make each engine mark every decision ruled on. The three engines now run side by side and stop at
  the first case any of them gets wrong, so a seeded-fault run ends early and the demo lane stays inside its time.

## 0.71.0

- `VG-ROB02-UNTRUSTED` (18.37b) is new, with 40 cases and three seeded faults, one in each engine's graph builder.
  ROB-02 says no untrusted content influences consequential decisions, but it only checked that a decision named
  an input, and a decision in a valid bundle never names one that way, so the rule never looked at trust. Now each
  engine follows content from the memory guard's records and an instruction's source class to every decision it
  reached, through inputs, the reads a decision or a call consumed, tool calls, model calls, other decisions and a
  decision's origin, for as many hops as the records show. A decision that untrusted, quarantined or blocked
  content reached fails. Each case checks ROB-02's outcome, the failing decisions and the exit code in all three
  engines, with nothing quarantined.
- `VG-CATALOG-RULE-UNIQUE`'s baseline drops the eu-ai-act pair DAT-01/ROB-02 (18.37b): the two controls no longer
  share a rule, and the pair ceiling falls from 25 to 24.

## 0.70.0

- `VG-CLI-COLLECT-DOCTOR-INIT` (18.112) is new, with 33 rows and eight seeded faults, and covers the command lines
  of `collect`, `doctor` and `init`. Python's `init` accepted `--non-interactive` and did nothing with it. Given
  both `--project` and `--write-errors`, `doctor` wrote the message-key catalogue and never read `--project`, even
  for a directory that does not exist. Given an empty `--write-errors`, it diagnosed instead. `collect --dry-run`
  never read `--adapters-root` and dropped an empty `--out`. Its help said a dry run wrote nothing, but with
  `--out` it writes the plan and its manifest. Python read an empty `--adapters-root`, `init --out`, `--subject`
  or `--framework` as the default. The rows check that each engine refuses those lines with a key at exit 3 and
  writes nothing, and that a dry run with `--out` writes `collect-plan.json` and `manifest.json`. The three commands are Python only, so
  TypeScript and Java answer every row with `cli.not_implemented`.
- The gate entries in `gates.json` are in a new order (18.112), so the seeded-fault demo lanes carry a balanced
  load. No gate, row, fault or timeout changed.

## 0.69.0

- `VG-CLI-UTILITY` (18.111) is new, with 121 rows and twelve seeded faults, and covers the command lines of `validate`, `diff`,
  `version`, `conformance` and `conformance run`. TypeScript and Java said the bundle was missing for
  `validate -h`, read `diff -h` as a file, printed the version for `version --help` and for any argument, and
  refused `conformance -h` as an unknown action. They ran past unknown flags and extra arguments, ignored the
  `=` form, took the first of a repeated `--bundle` or `--format`, and accepted `conformance run --adapters`
  without reading it, even for a directory that does not exist. Python read an empty `--bundle`, `--engine`,
  `--corpus` or `--adapters` as the working directory, wrote `validate --out ''` into it, dropped an empty
  `conformance run --out`, took an empty `diff --format` as text, and refused `diff A --format md B`. The rows
  check that each engine refuses those lines with a key at exit 3, prints the same usage for `-h`, reads diff's
  files on either side of `--format`, and runs the adapters' own orchestrator for `--adapters`. The
  orchestrator's claim goes into the report and sets the exit code. They read a committed empty corpus, a one-project corpus written
  by the corpus generator, and nine sample adapters directories: claim full, partial and none, and four whose
  counts each engine must read as Python's `int()` does (two past 2**64 that differ by one, the string `"1.0"`
  and `null`, both refused with `internal.unexpected`, and a `round_trip` of `0.0`, which is false), and two that
  write a byte that is not UTF-8 on stdout or stderr, refused with `internal.unexpected` as Python's strict decode
  refuses it. A case's
  `has` can now list several paths, and its `file_says` names a token a written file must hold.
- `VG-CLI-OPTIONS` (18.111): its `validate`, `diff`, `conformance` and `version` rows no longer need a
  TypeScript or Java override, and its Java unknown-flag fault seeds the same fault in the rewritten scanner.
- `VG-CLI-ASSESS` (18.111): its TypeScript `-h` fault and Java unknown-flag fault seed the same faults in the
  rewritten scanners.

## 0.68.0

- `VG-CLI-REPORT` (18.110) is new, with 85 rows and seven seeded faults, and covers `report`'s command line.
  TypeScript and Java refused `--format public`, wrote the same pack with or without `--role` and accepted
  `--role nope`. They said the assertions file was missing for `report -h`, ran past unknown flags, ignored
  the `=` form and took the first of a repeated `--from`. Java wrote English for `--language de`, ended its
  OSCAL and SARIF renderings with a newline Python does not write, counted a `--catalog` label of no-break
  spaces as a label, and TypeScript cut a piped rendering at 64 KiB. All three engines accepted any
  `--language`, an empty `--catalog` label, `--role`, `--catalog` or `--language` with a format that never
  reads it, and any rendering option beside `--validate`. Each engine now reads report's options from one
  declared table and refuses those lines with a key at exit 3; `--format public` and the role packs are
  byte-identical across the three. The rows read a committed `assertions.json` with two deviations, a Conduct
  control and a subject with Markdown-special characters. A case's `says` can now list several tokens, and a
  case's `late_reader` holds stdout unread until the command exits, so a piped rendering cut at exit is caught
  on any machine.
- `VG-CLI-OPTIONS` (18.110): its three `report` rows (`--he`, `--form md`, `--no-such-flag`) no longer
  need a TypeScript or Java override.

## 0.67.0

- `VG-CLI-ASSESS` (18.109) is new, with 55 rows and seven seeded faults, and covers `assess`'s command
  line. TypeScript and Java said the bundle was missing for `assess -h`, ran the assessment when `-h` sat
  beside `--bundle`, ignored the `=` form of most options, took the first of a repeated option, and wrote
  an English report for `--report-language de`. All three engines accepted `--manual` and `--probes`
  without reading them, any `--report-language`, and `--allow-unverified-catalog` with no `--catalog-dir`.
  Each engine now reads assess's options from one declared table. All three refuse those lines with a key
  at exit 3, and Java refuses a records folder with `input.records_unsupported`, not
  `input.bundle_missing`. The gate runs the same helper as `VG-CLI-OPTIONS` on its own case file, which
  can now also require a path the run writes (`has`) and a file's first line (`first_line`).
- `VG-CLI-OPTIONS` (18.109): the value-less `assess --out` row no longer needs a TypeScript or Java override, and its
  Java unknown-flag fault, with the Java and TypeScript `--deviations` and `--fail-on` faults of
  `VG-DEVIATIONS-PARITY` and `VG-FAIL-ON-PARITY`, now seeds the new scanners (`Argv.java`, `argv.ts`).

## 0.66.0

- `VG-CLI-TOP-LEVEL` (18.108) is new, with 40 rows and six seeded faults, and covers the top-level command
  line. TypeScript and Java answered `agentce`, `agentce --help`, an unknown command and `agentce --json
  version` with "not yet implemented", and ran the test seam `digest-tree` as if it were a command. All
  three engines now read the tokens before the command the same way. They print the same usage screen,
  and refuse an unknown command or flag with `input.unknown_command` at exit 3. Every engine refuses
  `--version` with anything else on the line; Python used to drop the rest. The gate runs the same helper
  as `VG-CLI-OPTIONS` on its own case file, so each gate's seeded-fault demo fits in its CI lane.
- `VG-CLI-OPTIONS` (18.108) now runs TypeScript through the npm bin, so it covers the command npm users
  run, and its unknown-command row no longer needs a TypeScript or Java override.
- `VG-SECURITY-VIEW`, `VG-DEVIATIONS-PARITY` and `VG-FAIL-ON-PARITY` (18.108) call the test seams from
  their new entry points (`dist/seams.js`, `org.agentce.Seams`): the seams are no longer CLI commands.

## 0.65.0

- `VG-CLI-OPTIONS` (18.107) grows from 45 to 79 rows and from nine to eleven seeded faults. Python
  answered most mistyped command lines with argparse's usage text and no message key. It read an
  abbreviation such as `--bun x` as `--bundle x`, and it took `-hx` as `-h`, printed the usage and dropped
  the `x`. Every Python argv error now exits 3 with a key, the same key TypeScript and Java give where
  they have one, and every Python parser refuses an abbreviation and a clustered short option. The new
  rows cover `-hx` and `-hh` on six commands, a `-h` cluster after a real target or after a value that
  starts with a dash (`--out -1`), unknown flags, value-less flags, a bad choice and an unknown action.
  Where TypeScript or Java still answer another way, the row's override names the item that changes it
  (18.108 to 18.111). `VG-FAIL-ON-PARITY` and `VG-DEVIATIONS-PARITY` now expect Python's key for a
  value-less flag. To keep the seeded-fault demo inside its CI lane, the gate's helper skips a build whose
  inputs hash the same as at its last build, and a red run stops at its first failure.

## 0.64.0

- `VG-DOC01-COMPONENTS` (18.37j) grows from 15 to 20 cases and from three to seven seeded faults. DOC-01
  read conformant in three cases where nothing, or the wrong thing, had been compared. An unpinned
  declaration of a model in one manifest cancelled another manifest's pin, so a call on a version no
  manifest pins passed; a component's pins are now the union of every pinned declaration, in either
  order. A manifest that declares only skills or prompts, with no call recorded, read conformant; it is no
  longer a component record, so DOC-01 reads not applicable under a declared profile. And a component
  record missing its `componentDeclared` or `declaredComponentsObserved` literal passed `sh:in`; DOC-01's
  shape gains S0, which requires one of the two on every record. Two of the new faults drop only a `true`
  literal, so only S0 turns the gate red.

## 0.63.0

- `VG-CLI-OPTIONS` (18.106) gains 20 `quickstart` rows. TypeScript and Java used to read quickstart's
  argv by looking for `--out` alone: they ran the quickstart and exited 0 on any unknown flag
  (`--trust-root` among them), a positional, `--out=<dir>`, a value-less `--out` and `-h`, took the first
  of two `--out`s, and printed nothing for `--debug`. Every engine now refuses a flag or argument
  quickstart does not take with `input.quickstart_unrecognized_flag` (Python included, which stops
  reading `--ou` as `--out`), reads `--out` as Python does, prints the usage for `-h`/`--help` and writes
  Python's two `--debug` records. Each case now runs in a fresh working directory with `--json` right
  after the command, and a case can require what the run writes, a token the output names, identical
  stdout across engines and the `--debug` records. The demo has four more seeded faults, nine in all.

## 0.62.0

- `VG-CLI-OPTIONS` (18.105) is new: one shared table of command lines, each run through the real Python,
  TypeScript and Java CLIs, with the exit code and refusal key each engine must give. A refused run must
  write nothing under `--out`. It starts with 18 `assess --for`/`--emit` rows. TypeScript and Java used to
  exit 0 and ignore both flags, even an unknown format or the two flags together. They now give Python's
  refusals in Python's order, and refuse any other `--emit` or `--for` with `input.emit_unsupported` until
  18.16b ports the formats and presets. Seven more rows cover an unknown flag, a shortened one (`--em`,
  `--fo`, `--ou`), a misspelling, another command's flag (`--format`) and a second positional, each refused
  with `input.assess_unrecognized_flag` in every engine. Later command-line items add their rows. The demo
  has five seeded faults, at least one per engine.
- `VG-PROJECT-VIEW` and `VG-I18N-ONE-CATALOGUE` (18.105): the TypeScript and Java runs drop
  `--for risk-lead`, which those engines now refuse. They render the project view whenever the profile
  names more than one subject, so the files the gates compare are unchanged.

## 0.61.0

- `VG-DOC01-COMPONENTS` (18.37a) is new. DOC-01, "Operating components match the declared documentation",
  used the same rule as REC-01 (a consequential decision names its agent), so it passed a subject whose calls
  used a tool nobody declared. It now compares a subject's `BundleLoaded` manifests with its `ToolCall` and
  `ModelCall` events. A call to a tool or model no manifest declares fails, and so does a call whose pinned
  version differs from the declared one. A model or MCP server that a manifest declares but no call uses also
  fails. Calls with no manifest read insufficient evidence, and a subject with neither reads not applicable.
  The gate builds 15 bundles and checks DOC-01's outcome, failing events and exit code from the real Python,
  TypeScript and Java CLIs. The demo has three seeded faults, one in each engine's graph builder.
- `VG-CATALOG-RULE-UNIQUE` (18.37a): DOC-01 no longer shares a rule with REC-01, so the baseline catalog's
  pair list is empty and its ceiling drops from 1 to 0, and eu-ai-act's drops from 26 to 25. The ceiling
  fault now raises baseline's ceiling from 0 to 1.
- `VG-QUICK-PATH` (18.37a): step 2, the records folder's derived bundle and profile run through
  `assess --bundle`, no longer expects every control to be not applicable. The records hold a `ToolCall`
  and no manifest declaring it, so DOC-01 reads insufficient evidence and the run exits 2. Every other
  control still reads not applicable.

## 0.60.0

- `VG-TRUST-ROOT-PARSE-PARITY` (18.81) is new. The trust root an operator passes with `--trust-root` or
  `AGENTCE_TRUST_ROOT` (and, in Python, `verify --report`'s signer or embedded trust root) is now read with
  the same strict JSON rule all three engines use for the files `verify` reads. Before, a trust root nested
  thousands of levels deep got a raw recursion error from Python and Jackson's own limit text from Java, and
  TypeScript loaded one such file, or a file with an invalid UTF-8 byte, as an empty trust root and ran the
  assessment. Now all three refuse a trust root nested more than 1000 levels deep with exit 3,
  `input.trust_root_invalid` and the same cause: `<path> is not readable JSON: it nests containers more than
  1000 levels deep`. NaN, an escaped lone surrogate, an invalid UTF-8 byte and a byte-order mark are refused
  in all three as well. The gate runs each engine's unit tests and the real CLIs on trust roots it generates.
  The demo has three seeded faults, one in each engine.

## 0.59.0

- `VG-VERIFY-REFUSAL-KEYS` (18.64) is new. Every reason `agentce verify` gives for a catalog or release it
  refuses now comes from a catalogue entry with a stable key and a fix in `docs/errors.md`, in Python,
  TypeScript and Java. A `verified: false` result lists those keys in a new `reason_keys` field, in the
  order their texts appear in `reason`. A wrapped refusal such as `no signature verified against the trust
  root: signature does not verify` lists both keys. Two reasons were Python exception text and are
  sentences now: `'sig'` reads `a signature entry has no sig field` and `'envelope'` reads `a signature
  entry has no envelope`. A file nested more than 1000 levels deep was `not readable JSON`; it is now
  `verify.json_too_deep`, which names the file, and all three engines find it before any other problem in
  the same bytes. The gate runs each engine's unit tests, scans the engine sources for a hand-copied
  cause, and runs the real CLIs on nine committed catalog signatures and five releases built from the
  development keys. With `--marker-only` it runs each engine from a copy of its catalogue with every
  `verify.*` cause marked, and checks that the marks in each reason match its keys. The verify census in
  `tools/verify_parity_check.py` now also checks that every `verified: false` result has keys that are in
  the catalogue and rebuild its reason, and that every row nested past the limit is `verify.json_too_deep`.
  The demo has three seeded faults, one in each engine.
- `VG-SIGN` and `VG-VERIFY` (18.64): three seeded faults follow the refusal code's new shape; what each one breaks is unchanged.

## 0.58.0

- `VG-I18N-ERROR-CATALOGUE` (18.80) is new. TypeScript and Java now hold an error-key registry read from
  the vendored `spec/i18n/` catalogue, as Python's `error_catalogue` does, and each engine's unit test
  checks that every literal key its own code raises is in the catalogue. That check found four keys
  Python, TypeScript and Java raised with no catalogue entry (`input.report_format`, `input.emit_format`,
  `input.fail_on_invalid_expression`, `input.collect_config`); they are in the catalogue and
  `docs/errors.md` now. A missing catalogue is empty in every engine. Every engine refuses a malformed
  one, or one whose top level is not an object (TypeScript returned `{}` for both, Java an empty map for
  the second). A malformed trust root gets one cause per shape in all three engines, such as `keys entry
  'abc' has no public_key`, where Python printed its own exception text and TypeScript and Java a base64
  error. A digest read that fails in `assess` is `input.digest_unreadable` (it was
  `internal.unexpected`). The gate runs the real CLIs on eight committed trust-root fixtures. The demo has
  nine seeded faults, at least one in each engine.
- `VG-I18N-ONE-CATALOGUE` (18.80) now fails when a TypeScript test file or Java test class it names is
  missing or declares no tests: node skipped a missing file and counted a file with no tests as one passing
  test, and Gradle passed while any other class matched. Three new seeded faults point each leg at a
  deleted file, and the TypeScript leg at a file with no tests. The two loader faults follow the
  loaders' new code.

## 0.57.0

- `VG-PSP-SHAPE-PROFILE-ENFORCED` (18.78) is new. Python, TypeScript and Java now refuse every catalog shape
  that `spec/rules/psp_check.py` refuses, not only `sh:sparql` and `sh:js`: `sh:closed`, the logical
  combinators, unbounded paths, paths longer than three, unanchored patterns, ranges on other types,
  nested `agentce:targetWhere` and any SHACL term the profile does not permit. The term lists live in one
  file, `spec/rules/psp-terms.json`, which the checker reads and each engine ships a copy of. A refusal
  is `catalog.shape.outside_profile` and names the feature and the shape file; a shape file that will
  not parse is `catalog.shape.parse_error` in every engine (it was `internal.unexpected`, and Python
  accepted an IRI escape like `\uZZZZ`). `tools/psp_profile_check.py` runs 59 pinned scenarios through
  the three engines' `assess --catalog-dir` (Python under two hash seeds) and Python's `catalog lint`.
  Nine seeded faults. Building it found Java's Turtle reader could not read a decimal such as `1.5`,
  a typed literal such as `"2026-01-01T00:00:00Z"^^xsd:dateTime` (the only date bound the profile
  allows) or a language tag; it reads all three now. Measured: one gate run 39 s on a 10-core laptop;
  the nine-fault demo 5 min 56 s on one CI demo-fault shard (18 gate runs, about 20 s each).
- `VG-CATALOG-SHAPE-SAFETY` (18.34) keeps its three faults, now aimed at the shared profile check.

## 0.56.0

- `VG-RECORDS-FOLDER-PARITY` (18.69) is new. TypeScript now reads a records folder with
  `agentce assess <folder>`, and this gate holds its answer to Python's. `tools/records_folder_parity_check.py`
  runs 42 scenarios, each as text and with `--json` in a fresh tree, through both engines, and compares
  exit codes, refusal keys, the records summary and lines, the out folder's file list, and the bytes of
  the bundle, the derived `applicability.yaml` and the results. The scenarios cover the reference set,
  file names and agent ids whose UTF-16 order differs from their code-point order, tool and model names
  for each PyYAML quoting path, nesting at 256 and 257 (also inside a JSON Lines line and behind a BOM),
  lone surrogates, `NaN`, invalid UTF-8, CRLF JSON Lines, 25 bad lines, compressed files and symlinks,
  every refusal (each must exit 3 and leave the out folder as it was), profiles passed back (unedited,
  reversed, trimmed, and 18.77's id-less records), and a newcomer's relative forms from one working
  directory. The hostile scenarios' `not read:` lines are pinned in the gate. Eight seeded faults, one per
  rule. Running it found four TypeScript differences, now fixed: `NaN` and `Infinity` in a trace export
  were refused where Python reads them; `applicability.jsonl`, `integrity.jsonl` and `quarantine.jsonl`
  wrote non-ASCII raw where Python escapes it, and `quarantine.jsonl` did not sort its keys; and a
  profile was read with YAML 1.2 rules, so a tool named `0o17` or `1e10` that PyYAML writes unquoted
  came back as a number. Java joins in 18.69b.

## 0.55.0

- `VG-OTEL-GENAI-PARITY` is hardened. An earlier fix replaced the schema-URL trailing-slash regex in
  TypeScript (`/\/+$/`) and Java (`replaceAll("/+$", "")`) with a linear scan, since the regex took over
  180 s on one million slashes followed by text, but no vector pinned it. The new hostile vector
  `long-slash-run-schema-url` carries that schema URL (30 vectors now), and
  `tools/otel_genai_adapter_check.py` gives each TypeScript and Java run a fixed 30 s per vector; a run
  past it fails with a `TIMEOUT` line naming the vector and the engine. Two seeded faults added: TypeScript's
  scan reverts to the regex, and Java's reverts to `replaceAll`.

## 0.54.0

- `VG-RECORDS-PROFILE-ROUND-TRIP` (18.77) is new. A records-folder run writes `applicability.yaml` with the
  line "Edit it ... and pass it back with --profile", but a folder with two or more agents gave a profile that
  AgentCE refused when it was passed back. A profile that declares several subjects now works over a records
  folder: each record stays on the agent id it carries, an agent the profile does not name gets its own row
  and report and is listed as undeclared, and a record with no agent id goes to `agentce:subject/local`. The
  gate runs four scenarios: the unedited round trip gives byte-identical assertions with every agent declared;
  a removed agent is undeclared, and so are its tools and models; id-less records never land on a named agent;
  a reversed profile gives the same result. It has four seeded faults, one for each rule the scenarios check.
  The error key that refused such a profile is retired, since no case raises it now. Python only until
  TypeScript and Java gain the records-folder mode (18.69).
- `VG-PROJECT-TIME` (18.77): its fault that marks a fresh multi-agent run's agents declared now edits the line
  that sets the declared ids from the profile's own, the line 18.77 moved it to. The fault and its effect are
  unchanged.

## 0.53.0

- `VG-DEMO-SHARD-COVERAGE` (18.76) is hardened. At the workflow root it refused only
  `defaults.run.shell`, so a root `env:` that sets `BASH_ENV` got through. Bash sources that file
  before every `run:` step under GitHub's default `bash -e {0}`, so a file that exits 0 turned
  `quick` green while `build` had failed; a scratch-branch run on `ubuntu-latest` showed it. The
  root may now carry only `name`, `on`, `permissions` and `jobs`, the same allowlist approach the
  two jobs already use. `VG-VERIFY-CENSUS-SHARD-COVERAGE` shares the root check, so
  `quickstart.yml` gets the same protection. One seeded fault added: a root `env:` with a
  `BASH_ENV` payload.

## 0.52.0

- `VG-REPORT-BRANCH-COVERAGE` (18.75) is hardened. coverage.py does not count a line marked `# pragma: no
  cover` or a branch marked `# pragma: no branch` as missing, so a pragma let an untested branch in
  `_verify_report` pass. The gate now reads coverage.py's own exclusion and partial-branch patterns and
  fails on any matching line in `_verify_report`'s span, nested functions and decorators included, and
  on any excluded line in the report. It also holds `_has_expect_keyid`, which coverage.py reports as
  its own region, to 100%; a new test covers its untested branch. Three seeded faults added: a
  no-cover pragma on an untested branch, a no-branch pragma, and an untested branch in the nested
  function.

## 0.51.0

- `VG-RECORDS-EXIT-CODE` (18.72) is new. A records-folder scan (`agentce assess <folder>`) never returns
  exit 2 on its own, so a first run over the records a team already keeps is not a failure; CI that
  should fail on evidence gaps opts in with `--fail-on 'outcome=="insufficient_evidence" and
  severity=="high"'`, which returns 1. Nine scenarios over a gate-only catalog and the default one: a
  records run exits 1 or 0, never 2, with or without a declared `--profile`; the same evidence as a
  formal `--bundle`/`--profile` run exits 2; the opt-in, the docs recipe's combined form and an opt-in
  that matches nothing give the expected `fail_on`; an unquoted literal is refused with nothing written.
  Three seeded faults: the exit-2 rule applies to records runs, a declared `--profile` makes a records
  run formal, or `--fail-on` is ignored for records runs. Python only until TypeScript and Java gain
  the records-folder mode (18.69); 18.72a then runs it in all three.

## 0.50.0

- `VG-FAIL-ON-PARITY` (18.73) is new. TypeScript and Java accepted `assess --fail-on <expression>` and
  ignored it, so a run Python passed failed in the other two, and a malformed expression was not
  refused. The gate runs 38 scenarios through each engine's real CLI and needs Python's exit code,
  exit_status and `fail_on`, or Python's refusal with nothing written; a value flag with no value must
  give argparse's sentence. It also runs Python's `parse_fail_on` and the TypeScript and Java
  `fail-on-check` seams over 6,051 expressions, 6,000 of them fuzzed from a fixed seed, and needs the
  same bytes from all three. Ten seeded faults: TypeScript ignores the match count, counts UTF-16 units,
  uses JavaScript's whitespace, keeps an escape's backslash, or reads only `--fail-on <expression>`;
  Java's identifier uses `Character.isLetterOrDigit`, its flag scan keeps the first value, it parses
  after the catalogs resolve, or it treats `and` as `or`; Python evaluates the OR-groups with `all()`.
- `VG-DEVIATIONS-PARITY` (18.73): its two Java faults on `--deviations` (the `=` spelling and the
  first value kept) now anchor on the value-flag scan `--deviations` and `--fail-on` share, with the
  same effect.

## 0.49.0

- `VG-JSON-INTEGER-LIMIT` (18.71) is new. An evidence line or report artifact carrying an integer
  literal over 4300 digits is invalid JSON in all three engines: `validate` and `assess` quarantine the
  line as `schema_invalid`, the same as a malformed line, and `report --validate` names the artifact.
  4300 digits, with or without a sign, still parse. Python gives the same results under
  `PYTHONINTMAXSTRDIGITS=0` and `=640`, so the interpreter setting cannot change what a file means. Four
  seeded faults: Python trusts the interpreter's limit, Python lets the bare `ValueError` escape, Python's
  JSONL line check goes back to `json.loads`, and TypeScript's report check uses `JSON.parse` alone.

## 0.48.0

- `VG-VERIFY` (18.68) now checks what its title says. Its legs were all key-signed, though the title
  says "key-based or certificate-based". Three new legs, in all three engines: the valid keyless
  certificate release verifies with `keyless: true`; a kms entry carrying `"cert": null` verifies with
  `keyless: false`; and an evidence bundle with a stream file nobody can read is refused with
  `input.bundle_unreadable` (skipped as root, where a mode-000 file still reads). Three new seeded
  faults: `keyless` back to `"cert" in signature` (Python), the unreadable classification removed from
  `loadBundle` (TypeScript), and `verifyCertificate` always refusing (Java).

## 0.47.0

- `VG-VERIFY` (18.63) also checks keyless certificates. Three certificates the development authority
  re-signed after one field change (an `ecdsa-p256` algorithm, a `not_before` with a space instead of
  `T`, a swapped window) must be refused in all three engines with the exact reason naming
  `verify.certificate_algorithm`, `verify.certificate_validity_malformed` or
  `verify.certificate_validity_inverted`. Three new seeded faults: the algorithm check removed
  (Python), the `not_before <= not_after` comparison removed (TypeScript), and the RFC 3339 check made
  to accept anything (Java).

## 0.46.0

- `VG-CRYPTO-INTEL-WHEEL` (18.62) is stricter. It accepted any Intel-macOS `cryptography` wheel anywhere
  in a lock, so a PyPy-only `pp*` wheel, or a universal2 wheel on an entry whose `resolution-markers`
  keep it off Intel macOS, passed while CPython on an Intel Mac would build from source. Every entry
  whose markers admit CPython on Intel macOS now needs a `cp*` or `abi3` Intel wheel, and a marker or
  lock the check cannot read fails it. Two new seeded faults, one per rule.

## 0.45.0

- `VG-DIFF-READINESS-PARITY` (18.59) is new. `tools/diff_parity_check.py` and
  `tools/readiness_parity_check.py` compare the TypeScript and Java ports of `agentce diff` and
  `agentce readiness` with Python byte for byte, but only `quickstart.yml` ran them; `VG-DIFF` checks
  Python's diff alone. The gate runs both checkers' self-tests and real runs. Four seeded faults,
  one per port per command.

## 0.44.0

- `VG-NO-ML-SKILL-LOCKS` (18.56) is new. The two skills' `uv.lock` files are gitignored, so a fresh
  checkout has none and the no-ml scan skipped both skills' dependency trees. The no-ml job now runs
  `python3 tools/vendor_skill_engine.py --write` before the scan, and `no_ml_check.py
  --require-skill-locks` fails when a skill's lock is missing or does not list the skill and
  `agent-conformance`. The gate runs the self-test, checks the step order in `no-ml.yml`, generates the
  locks and runs the real scan with them required. Four seeded faults: the workflow drops the vendor
  step, a missing skill lock passes, `skills/` is left out of the scan, and an empty skill lock passes.

## 0.43.0

- `VG-CLAIM-PARITY` (18.53) is new. The TypeScript and Java engines now write `claim.json` when they
  assess, so the gate rebuilds both and runs `tools/claim_parity_check.py`. It checks that each engine
  writes the same claim as Python over the quickstart project, the auditor-view fixture, a profile with
  no subjects and the 30 generated corpus projects; only the engine block and the `claim_id` over it
  may differ. Each engine also signs its own quickstart report, and the signature must verify against
  the written trust root. A damaged `claim.json` must get `sign.claim_malformed` from all three
  engines. Five seeded faults: TypeScript writes no claim, Java drops the deviations list, the
  `claim_id` is computed over the wrong body, Java stops refusing a `signatures` field that is not a
  list, and Java writes a claim with no assertions.

## 0.42.0

- `VG-VERIFY-CENSUS-SHARD-COVERAGE` (18.97) now checks that the four census shards split one list. A new
  quickstart job, `census-list`, prints the census list's sha256 with `verify_parity_check.py --list-sha256`.
  Each shard needs that job and passes the digest as `--expect-list-sha256`. A shard whose own list hashes
  differently stops before it runs an engine, so shards that land on different runner images can no longer
  split different lists while CI stays green. The gate requires that wiring, and it runs a real shard with a
  digest one hex digit off, which must refuse within 60 seconds. It finds the census script by its
  module name, so a full census spelled `python -m verify_parity_check` now fails it. It also requires quickstart's
  triggers to be exactly push (main, `phase/**`), pull request and manual dispatch, so a `paths-ignore` is
  caught. Five new seeded faults: the shard's digest argument removed, a `paths-ignore` added, a full census
  spelled `python -m`, a reference digest over a list one mutation short, and a shard that ignores its
  expected digest.

## 0.41.0

- `VG-VERIFY-CENSUS-SHARD-COVERAGE` (18.96) now also builds the census list with `CI=true` and with `CI`
  unset, and fails if the two differ. The list used to have 2,320 mutations on CI and 2,312 on a laptop.
  The census fixture's `assess` named no formats, so under CI it also wrote `report.junit.xml`, whose
  manifest entry added eight mutations. The fixture builder now passes `--emit` and no longer writes the
  report's summary into the CI job's step summary. A laptop now lists the same 2,320 mutations CI runs,
  and CI's list is unchanged. New seeded fault: the `--emit` argument removed.

## 0.40.0

- Added `VG-DEVIATIONS-PARITY` (18.17a): TypeScript and Java now apply a deviation register on `assess
  --deviations` (shape check, lint, expired entries reported as limitations, the register digest in the
  manifest, OSCAL risk entries) and compute the auditor view, where both used to refuse the flag with
  `input.deviations_not_yet_supported`. The gate runs 27 scenarios through all three engines' real CLIs
  and requires Python's exit code, error key, cause and fix for every refusal, and byte-identical
  assertions, OSCAL, activity and blind-spot files with the same limitations and digest for every run
  that succeeds. Each engine's test-only `auditor-view` seam must print the bytes of Python's
  `compute_auditor_view` over three fixtures in either input order. Nine seeded faults: expiry ignored,
  the OSCAL risk dropped, `by_clause` control ids in reverse order, the lint skipped, the
  `--deviations=<path>` spelling ignored, a lossy UTF-8 decode, the auditor view's sort skipped, the
  first of two registers kept, and a register path with a `.` segment named as typed rather than as
  Python's `pathlib` renders it.
- `VG-PROJECT-VIEW`'s rubric no longer calls its deviations scenario Python-only. The new gate holds
  TypeScript's and Java's `project.json` for that fixture to Python's bytes.

## 0.39.0

- Added `VG-VERIFY-CENSUS-SHARD-COVERAGE` (18.93): the cross-engine `agentce verify` census (2,320
  mutations through all three engines) moved out of quickstart's `installed-artifacts-offline` job, where
  it took 15 of 21 minutes, into a four-way `verify-census` matrix job. Each shard runs
  `tools/verify_parity_check.py --shard "<job-index>/<job-total>"`; the artifacts job runs the named
  scenarios once with `--scenarios-only`. The gate checks the shard step's wiring, the matrix size, that
  outside the census job the script is named only by its `--self-test` and `--scenarios-only` lines, that
  both census self-tests still run in the artifacts job, and that the real `--list-shard i/4` output covers
  the full list with every position exactly once. Four seeded faults: a matrix entry dropped, the step
  rewired to a literal shard total, the shard selection truncated, and the full census brought back into
  the artifacts job as `--shard 0/1`.

## 0.38.0

- Added `VG-BASELINE-LENS-ENGINES` (18.46, loophole L18.9): `verification/gates/baseline_lens_engines.sh`
  already built and ran all three engines for real over the quickstart bundle with its `catalogs:` list
  stripped, asserting each resolves to `baseline@2026.09` and that TypeScript's and Java's `assertions.json`
  are byte-identical to Python's -- but it was referenced nowhere in this registry, so nothing ever ran it.
  Matches the shape of its 6 already-wired `*_engines.sh` siblings (`VG-BLIND-SPOTS`, `VG-PROJECT-VIEW`,
  `VG-REPORT-VALIDATE`, `VG-SIGN`, `VG-VERIFY`, `VG-WHAT-THEY-DID`). Two seeded faults: the default-lens
  resolution refused instead of evaluating the baseline, and TypeScript's rendered `severity` field broken,
  breaking byte-identity with Python's `assertions.json`.

## 0.37.0

- Extended `VG-AUDITOR-VIEW` (18.17c): the auditor view's own OSCAL-section text now names that a deviated
  finding's OSCAL entry carries a matching `risk` -- a fact the engine already rendered (`render_oscal`,
  the gate's own `check_oscal_risk`) but never stated in the auditor's own prose. A new seeded fault
  reverts the catalogue text to its prior wording and is caught.

## 0.36.0

- Added `VG-REACH-TABLE` (18.41, VALUE-PROP.md "Reach", USER_EXPERIENCE.md R8): the locked reach
  headline ("Reads records from 400+ tools and 600+ cloud services, and the tool declarations of
  25,000+ MCP servers...") and a one-reader-per-standard table, generated from
  `spec/ingest/support-matrix.yaml`'s `producers`/`producers_meta` data, now appear on README.md, the
  docs and website Supported-sources page (`docs/build.py`'s `_reach_table_text`, spliced into
  `_ingest_body_text`), and the landing page's proof strip. `reach_numbers(matrix)` is a pure function
  computing every count directly from the matrix; the gate compares every surface's stated count,
  check date and source link (per row, via a typed `ReachRow` spec) against it, by row label. A count
  mismatch (primary or the deduplicated-total row's self-managed-only secondary count) raises
  `reach.table.count_mismatch`; a missing or wrong date/source raises `reach.table.missing_provenance`;
  a missing section raises `reach.table.missing_section`; a duplicated heading or row raises
  `reach.table.duplicate_section`; a section missing the locked headline raises
  `reach.table.missing_headline`; and a real count that has fallen under the headline's own floor
  (400 tools, 600 cloud services, 25,000 MCP servers) raises `reach.table.floor_violated`
  independently of what any surface currently states, so the gate still catches honesty drift even if
  every surface agrees with every other surface. The 10-case self-test proves each key fires alone.

## 0.35.0

- Added `VG-UX-NEXT-STEP` (18.40, USER_EXPERIENCE.md R4): an AST scan over every
  `InputError(...)`/`AgentceError(...)` call site in `engines/python/agentce` with a literal key and a
  literal fix. A key is registered in `spec/i18n/messages.en.json` with a non-empty fix; when every one
  of its literal-fix call sites agrees on the text, the registered fix is byte-identical to it. A call
  site reached only through a shared forwarder that composes its own key at runtime
  (`_require_dir`/`_require_file` and similar) has a non-literal key and is out of scope here (item
  18.40f); a key that also has a real non-literal second voice elsewhere (an f-string site, or a call
  through `load_untrusted_yaml` or a `_doctor_problem` override) is exempt from the byte-match rule, not
  only required to be non-empty. A fixed 10-key table (the 8 quarantine reasons minus `unknown_source`,
  `internal.unexpected`, `input.records_no_genai_spans`, `insufficient_evidence`) must name an actor
  other than the person at the terminal. `docs/errors.md` is checked against a
  real `agentce doctor --write-errors` run, and must have a row for every key the scan found raised. Closed 23
  previously-undocumented keys, 11 stale single-voice fix texts, and 10 missing actor phrases found this
  way. TypeScript and Java do not get an equivalent static scanner in this item (tracked as item 18.40e);
  `_require_dir`/`_require_file` and the other runtime-composed-key forwarders are tracked as item 18.40f.

## 0.34.0

- Changed `VG-BUYER-VIEW`: closed the five round-2 verifier minors on the buyer view (18.18b,
  `verdicts/P18-18.18-verifier-833b132.md` N1-N5). Four new fixture controls (BUY-05 non-conformant,
  BUY-06 partial via an unexpired `deviations.yaml` entry, BUY-07 not_applicable, BUY-08 not_assessed)
  close N1(a): the gate now asserts the exact per-outcome answer sentence for all six outcomes, not
  only conformant/insufficient_evidence. N1(b): a packaged run that writes no `buyer.md` now fails
  loudly instead of passing through a vacuously-true `elif`. N2: the buyer view's own
  `report.buyer_how_to_check_heading` ("How to check this report") replaces the shared auditor-view
  heading, and the evaluated catalog's id@version is now its own line, not only inside the reproduce
  command. N4: check (j)'s `grep | wc -l` pipeline no longer aborts the whole script silently under
  `set -euo pipefail` when it finds zero matches, and a new unconditional end-of-run marker line lets
  CI tell a graceful all-checks-ran failure from a silent abort. N5: a redundant second
  `sanitize_for_markdown` pass over the gap-step line no longer substitutes its own literal backticks
  into single quotes. The committed golden and the summary-table counts row both change accordingly
  (`| 3 | 1 | 1 | 1 | 1 | 1 |`), which is expected, not a regression. `rubric.pass` now names checks
  (i) through (n) explicitly, which this entry itself was the gap 18.17's own 0.15.0 entry warned
  about repeating.
- Changed `VG-AUDIENCE-PRESETS`: `title`/`rubric` text named only the six presets that predate the
  `buyer` preset (added alongside `VG-BUYER-VIEW` in 0.16.0, wired into
  `verification/gates/audience_presets.sh`'s own loop and fixture assertions at the time, same drift
  class as 0.15.0's) -- now names all seven.

## 0.33.0

- Added `VG-INGEST-SUPPORT-MATRIX`: a quick-tier gate checking that README.md's `**Supported**`/
  `**Experimental**`/`**Roadmap**` tier lines and the backend table in
  `website/src/content/docs/docs/trace-store-connectors.md` name ingest sources only from
  `spec/ingest/support-matrix.yaml`, under the tier the matrix grades them (18.39, MAINTAINER-NOTES
  2026-09-29 "web copy names sources only from the matrix"). `ingest_support_matrix_check.py` scans
  both copy surfaces (not just README, after the trace-store page was found still claiming Datadog
  LLM Observability "round-trips" at Supported confidence when the matrix grades it Experimental) and
  fails with one of eleven distinct message keys (`ingest.matrix.unknown_source`, `.wrong_tier`,
  `.count_mismatch`, `.count_arithmetic`, `.missing_tier_line`, `.docs_wrong_tier`,
  `.docs_unknown_source`, `.docs_table_missing`, `.docs_malformed_row`, `.docs_unrecognized_tier`,
  `.docs_definition_drift`), each proven by its own `--self-test` case against synthetic fixtures.
  Four seeded faults, each isolated to the one rule it tests (a README Roadmap name outside the
  matrix; an Experimental/Roadmap name swap that keeps both tiers' stated counts unchanged, so a
  count check alone cannot catch it; the trace-store page's old overstated Datadog claim; a
  trace-store table row naming a backend outside the matrix). The table row scan treats a row's
  leading and trailing `|` as optional, since GFM renders a table row whether or not it carries them.
- Added a registry-lint check: `gates.json`'s `suite_version` must equal the newest `## X.Y.Z` heading
  in this file. `VG-CI-FAST-CHECKS` shipped in 791ecf3 with the two already out of step (18.37i's own
  gate), caught only by hand; this closes that class for good.

## 0.32.0

- Added `VG-CI-FAST-CHECKS`: a quick-tier gate running every package's `ruff check`, `ruff format
  --check`, `mypy`, `pnpm lint` and `pnpm typecheck`, and both `conformance/` registry self-tests
  (`catalog_registry_check.py --self-test`, `registry_check.py --self-test`), in the same directory
  and with the same command CI uses (18.37i, AGENTS.md: "These mirror the CI workflows; keep them in
  sync"). 18.37's circuit breaker found two of its three failures were checks CI runs that
  `--quick` did not (a verifier round's ruff/mypy/biome findings; a red CI run from a stale catalog
  registry digest). `verification/gates/ci_fast_checks_check.py` scans every `.github/workflows/
  *.yml` file for each real invocation of one of those checkers, pairs it with its step's
  `working-directory`, and fails if a discovered CI step has no entry in its committed `MANIFEST`
  or a `MANIFEST` entry matches no real CI step, so this gate and CI cannot silently drift apart;
  only once that comparison is clean does `verification/gates/ci_fast_checks.sh` run the real
  commands. `offline_bundle`, `integration_breadth_check` and the docs build stay out of scope
  (too slow for the quick tier). Two seeded faults: `MANIFEST` drops its `engines/python` `mypy`
  entry while the real CI step stays (the drift this gate exists to catch), and an unused import in
  `verification/shard.py` trips `ruff check` for real (proving the gate's commands actually run, not
  only the drift comparison).

## 0.31.0

- Added `VG-CATALOG-RULE-UNIQUE`: no two rung-2 controls in any shipped catalog (the three base catalogs
  and the four overlays) share an identical shape and fixture set, so a control's rule always tests what
  its own title says (18.37, SPEC §7.3). Found live: DOC-01 shipped with REC-01's shape and fixtures
  verbatim in both baseline and eu-ai-act, ROB-02 shipped with DAT-01's shape and fixtures verbatim in
  eu-ai-act, and a further 24 unnamed eu-ai-act pairs were real duplicates nobody had caught. The check
  (`engines/python/agentce/catalog.py`'s `rule_uniqueness_problems`/`new_rule_uniqueness_problems`/
  `stale_rule_uniqueness_pairs`) is baseline-aware and shrink-only: `verification/gates/fixtures/
  rule_uniqueness/baseline.json` discloses today's 27 known pairs (1 baseline, 26 eu-ai-act; the four
  overlays confirmed to have zero), owned by 18.37a, 18.37b and 18.37c, and the gate fails on a pair not
  already named there (a genuinely new duplicate) or on a named pair that is no longer a real duplicate
  (a stale baseline entry) or on a catalog's disclosed-pair count exceeding its pinned `ceilings` entry
  in the same file (shrink-only enforced, not just asserted: quietly adding a new duplicate to `pairs`
  without also raising the matching ceiling now fails on the ceiling, rather than passing as
  "already disclosed"). The count must equal the ceiling. A pair paid down without lowering its
  ceiling would leave a slot for a later duplicate. Seeded faults: baseline's
  INC-01 edited to point at REC-01's shape and fixture files verbatim, and the baseline catalog's
  ceiling raised from 1 to 2 with no new pair.

## 0.30.0

- Added `VG-BYO-CATALOG-PARITY`: an operator's own `--catalog-dir` is untrusted input in all three engines
  (18.36, SPEC §8.7). TypeScript and Java now verify each catalog directory against the effective trust
  root (`--trust-root`, else `AGENTCE_TRUST_ROOT`, else the vendored root), as Python already did. The gate
  builds an untrusted, an unsigned, a trusted and a rebranded catalog with the real `agentce catalog sign`
  under a temporary directory, then runs 12 scenarios through each engine's real CLI and requires
  TypeScript and Java to match Python's exit code, error key, cause, fix and recorded limitation (envelope
  and `manifest.json`). A malformed trust root's cause is compared up to the JSON parser's own message.
  Six seeded faults: the signature check bypassed, the identity cross-check removed, and the trust root's
  top-level object check removed, each in TypeScript and in Java.
- Changed `VG-BLIND-SPOTS`, `VG-PROJECT-VIEW` and `VG-I18N-ONE-CATALOGUE`: every engine now gets
  `--allow-unverified-catalog` for the gates' deliberately unsigned fixture catalogs, since TypeScript and
  Java verify `--catalog-dir` signatures too.

## 0.29.0

- Added `VG-I18N-ONE-CATALOGUE`: TypeScript and Java load the vendored `spec/i18n/` message catalogue
  instead of hand-copied dictionaries (18.35, restores item 14.1's one-catalogue invariant). A direct
  `cmp` of `spec/i18n/` against both vendored copies, each engine's own sync test and a catalogue unit
  test that deep-equals a live read of the spec file (catching a reverted loader even if it still
  hard-codes a few correct-looking spot values, which the sync test alone cannot), then a real-CLI leg
  reusing `VG-PROJECT-VIEW`'s own fixture and `multi_args`: the delegate agent's own `report.md` must
  read exactly `- Agents: spiffe://corp/agents/project-view-fixture-delegate` under its "Not yet
  declared in your profile" heading, in all three engines. Four seeded faults: a one-byte drift in
  each engine's vendored `messages.en.json`, and each engine's `loadCatalog` reverted to a hand-copied
  literal.

## 0.28.0

- Added `VG-CATALOG-SHAPE-SAFETY`: catalog lint (Python) and catalog loading (all three engines) refuse a
  shape carrying `sh:sparql` or `sh:js` (18.34) -- each engine's shape parser otherwise only ever reads the
  specific SHACL predicates it recognises, so an unrecognised one like `sh:sparql` parses as an ordinary,
  incomplete shape and is silently accepted, letting a catalog carry logic no engine can evaluate
  identically. TypeScript and Java have no `catalog lint` command, so their scope is catalog loading only.
  A shape carrying both predicates always reports `sh:sparql` first, in every engine, matching
  `spec/rules/psp_check.py`'s own priority order. The gate also runs the real `agentce catalog lint`/
  `agentce assess --catalog-dir` CLI per engine, against both a mutated and a clean catalog, asserting the
  message key in the real output as well as the exit code. Three seeded faults, one per engine, each
  removing that engine's forbidden-predicate check.

## 0.27.0

- Changed `VG-PROJECT-VIEW`: the project view's per-agent row now carries a `deviations` list (18.14a,
  Hill 7's role-views clause, second half) -- a Python-only CLI-driven scenario
  (`verification/gates/fixtures/project_view_deviations/`, reusing `VG-AUDITOR-VIEW`'s own AUV-01
  SHACL pattern as a new control `PVD-01`) asserts subject A's applied, unexpired deviation surfaces
  with its real expiry in both `project.json` and the rendered `project.md`, and that subject B's row
  stays empty -- without disturbing the existing 3-subject fixture (its own golden gained only the
  new, always-present, empty `deviations` key). Two new seeded faults: the per-subject deviation loop
  reading every subject's assertions instead of its own (a leak), and the expiry-presence guard
  inverted (a real expiry goes missing while an unmatched control's is fabricated).

## 0.26.0

- Added `VG-QUICK-PATH`: the records-folder quick path (`agentce assess <folder>`, 18.32) proves the
  records-vs-bundle divergence on one pinned trace export -- the same derived evidence judges every
  control insufficient_evidence as a records run but not_applicable once reduced to a formal
  --bundle/--profile assessment, which then judges nothing (exit 3, `input.nothing_evaluated`) -- the
  checks_unlocked signal on a real scan through a gate-only catalog (one control missing a
  self-reported ModelCall), and that the derived profile's `pilot_window: true` keeps passing the
  agentce-get-evidence skill's own lint despite its short, exploratory window.

## 0.25.0

- Added `VG-DEMO-SHARD-COVERAGE`: the CI `demo-fault` job (18.74) partitions every gate's seeded-fault
  demo across a parallel matrix, each job running `verification/shard.py <job-index> <job-total>`
  rather than naming gates in the workflow file, so the sequential
  `for gate in $(./verification/run --list)` loop no longer has to run one gate at a time inside the
  required `quick` check's own timeout. `strategy.job-index`/`strategy.job-total` are GitHub's own
  contiguous numbering of every job the matrix actually creates, so the partition is complete by
  construction; this gate guards the three remaining ways the split can still silently stop proving
  something: the demo step rekeyed off a literal matrix value or an `if:`/`continue-on-error`; the
  required `quick` job's own gating logic losing its dependency on `demo-fault`'s result (GitHub
  treats a skipped required check as passing, so `quick`'s own step is exact-matched against its
  canonical dependency-check text, not a substring search); and `shard.py`'s own partition line
  truncating or duplicating a gate, which a wiring check alone cannot see -- checked by loading
  `shard.py` in-process and calling its `partition()` against the real gate list. Three seeded
  faults, one per failure mode.

## 0.24.0

- Added `VG-REPORT-BRANCH-COVERAGE`: `_verify_report`'s own reproduction steps
  (`engines/python/agentce/commands/__init__.py`) are 100% branch-covered by the Python engine's own
  `test_verify_report*` tests. `_verify_report` has about 15 raise sites across its 9 reproduction steps,
  several sharing a message key, so an exit-code/key check alone cannot tell two such branches apart --
  three separate verifier rounds on item 18.8 each found a live mutation an exit-code/key check missed
  (item 18.8.R1). Wraps `tools/verify_report_branch_coverage_check.py`'s `--self-test` (proving the
  comparator discriminates a missing branch before trusting it) and its real run, which collects
  coverage.py branch data from the `test_verify_report*`-named tests alone and reads coverage.py's own
  per-function report for `_verify_report`. Its seeded fault disables the outer, signed `bundle_digest`
  equality check (mutation M8).

## 0.23.0

- Added `VG-ASSESS-EXIT-INSUFFICIENT-EVIDENCE`: `assess` returns exit code 2 (insufficient evidence on
  any `severity: high` control) identically across Python, TypeScript, and Java, over four real corpus
  projects (`credit/langgraph/insufficient-evidence` -> 2, `known-pass` -> 0, `known-fail` -> 1,
  `corpus/quickstart` -> 0) (item 18.30, SPEC.md:1076 / SPEC Sec.8.5). Wraps
  `tools/assess_exit_code_parity_check.py`'s `--self-test` (proving the comparator discriminates a
  one-value tamper before trusting it) and its real cross-engine invocation over fresh builds of the
  TypeScript `dist/` and the Java runnable jar. Its three seeded faults each drop one engine's
  severity-high `insufficient_evidence` check entirely, so that engine never adds exit code 2 regardless
  of its own assertions.

## 0.22.0

- Added `VG-OTEL-GENAI-PARITY`: the otel-genai adapter's translation layer (bytes -> canonical
  AgentCE events) is byte-identical across Python, TypeScript, and Java over all 9 vendor fixtures
  (`adapters/otel-genai/fixtures/`) plus all 16 hostile vectors
  (`spec/model/test-vectors/otel-genai-hostile/`), 25 vectors in total (item 18.29). Wraps
  `tools/otel_genai_adapter_check.py`'s `--self-test` (proving the comparator itself discriminates
  the truncation/enum-filter fault class before trusting it) and its real cross-engine invocation
  over fresh builds of the TypeScript `dist/` and the Java runnable jar. Its seeded fault drops the
  tool-protocol enum filter from the TypeScript port, caught specifically by the
  `truncation-and-enums` vector's out-of-enum `protocol` value.

## 0.21.0

- Added `VG-VERIFY`: a tampered or unsigned catalog or release artifact is refused with a stable,
  non-crashing result in all three engines, and a validly-signed one -- key-based or certificate-based --
  verifies offline (item 18.28). Reuses `tools/verify_parity_check.py`'s own cryptographic fixture builder
  (18.28's C3) rather than a second, independently-drifting fixture pair. Its three seeded faults each flip
  only one engine's leg red: Python's `_verify_release` loses the try/except that is this item's own fix
  (reintroducing the crash the item names), and TypeScript's and Java's `verifyRelease` each become a stub
  that always answers `verified: true`, proving the gate's third leg (a validly-signed release must still
  verify) cannot be passed by a stub that never checks a signature.

## 0.20.0

- Added `VG-CRYPTO-INTEL-WHEEL`: every tracked `uv.lock` that resolves `cryptography` resolves a real
  Intel-macOS wheel (item 18.58). ADR-0024 split `engines/python/pyproject.toml` and
  `adapters/supply-chain/pyproject.toml`'s `cryptography` requirement by platform marker so Intel macOS
  gets the last release with a prebuilt wheel (48.x); 18.55's verifier found 20 other tracked locks
  across the workspace (adapters, conformance, corpus, docs, `engines/python-emit`, `spec/rules`, the
  per-framework examples) still pinned to plain `cryptography==50.0.1` with no Intel wheel, because each
  depends on `agent-conformance` transitively and nobody had re-locked them against the split. Its seeded
  fault disables the matcher so it never reports a problem, so a later bump that drops the platform split
  anywhere would otherwise pass silently.
- `gates.json`'s `suite_version` is bumped from `0.18.0` to `0.20.0`, catching up the `0.19.0` bump
  `VG-REPORT-VALIDATE` (below) recorded here without updating the field.

## 0.19.0

- Added `VG-REPORT-VALIDATE`: `report --validate` performs real schema-validation of report
  artifacts, including the two-stage real-third-party-standard check for `oscal-ar.json`, in all
  three engines (18.27, contract critic round 2 F7). Reuses
  `tools/report_validate_parity_check.py`'s own `build_base_report`/`seed_oscal_nist_fault` to build
  a shared report directory and its real-schema-only OSCAL violation, matching `VG-SIGN`'s
  own-fixtures-reuse precedent. Its seeded fault disables Python's second-stage NIST OSCAL check, so
  a document satisfying only AgentCE's bounded local profile is wrongly reported valid.

## 0.18.0

- Added `VG-SKILL-RELEASE-SHAPE`: once everyday commits stop tracking the two agent skills' vendored
  engine wheel and lock (18.54), the commit a release tag points at still force-adds and commits them,
  so a commit-pinned checkout of that tag keeps installing standalone and offline exactly as
  `skills/README.md`'s "Install and pin (S-10)" documents. Builds a throwaway release-shaped commit
  with `tools/skill_release_shape_check.py`, extracts each skill folder from the committed tree alone
  (never the worktree's working directory), and runs both skills' standalone self-tests offline; also
  rebuilds each wheel fresh from that commit's own `engines/python` and confirms it matches the
  committed one, catching a release commit vendored before a later, unvendored engine change. It also
  fails when the commit under test tracks a wheel or lock under `skills/` and any branch contains it, and
  its self-test shows that `vendor_skill_engine.py --release` refuses to run on a named branch. Its seeded
  faults swap the check's `--release` call for a plain `--write`, so no release commit is ever cut;
  drop the branch-wheel check from the standing checks; and make every commit look like one no branch
  contains.

## 0.17.0

- Added `VG-SIGN`: the engine refuses to sign a report that is not ready to publish, and a
  `kms`-profile signature verifies offline against the published public key, in all three engines.
  Builds the READY/NOT_READY report directories with `tools/sign_parity_check.py`'s own fixture
  builders (the same ones its own cross-engine parity check uses) rather than a second,
  independently-drifting fixture pair, then runs `sign` against each of the built Python, TypeScript
  and Java engines and independently verifies every produced signature against the known test key via
  Python's own `signing.verify_envelope`.

## 0.16.0

- Added `VG-BUYER-VIEW`: the buyer view (18.18, Hill 8's buyer lens: "does this vendor's agent meet
  what I asked?") renders generated CAIQ/AI Controls Matrix questionnaire answers grouped by question,
  a one-page summary, and how to check this report -- matching a committed golden byte-for-byte on a
  dedicated fixture (`verification/gates/fixtures/buyer_view/`, a four-control catalog over one shared
  event that carries both `agent` and `time`, unlike the auditor_view fixture's event). BUY-01's own
  evidence event id and BUY-02's own clause id each carry a distinct `<script>` fragment that every
  rendering must escape, never emit raw; BUY-03 (mapped only to eu-ai-act) and BUY-04 (no crosswalk
  entry) must both stay absent from the answer set ("maps to nothing in scope, not nothing").
  `buyer.json`'s `counts` are checked against a fresh `aggregate()` over the same run's own
  `assertions.json`, never a `report.json` (which does not exist).

## 0.15.0

- Changed `VG-AUDIENCE-PRESETS`: the `auditor` preset (added alongside `VG-AUDITOR-VIEW` in 0.14.0) was
  wired into `verification/gates/audience_presets.sh`'s own loop and fixture assertions but the gate's
  registry `title`/`rubric` text in `gates.json` still named only the five presets that predate it
  (18.17 round-2 verifier finding N11) -- now names all six.

## 0.14.0

- Added `VG-AUDITOR-VIEW`: the auditor view (18.17, Hill 8's auditor lens: "can I rely on this, clause
  by clause?") renders every clause with its full evidence trail, a `by_clause` navigation index, an
  accepted deviation's full owner/approver/rationale detail, an expired deviation ignored and reported
  rather than applied, a `manual`-mode control's disclosed not-yet-evaluated note, and the OSCAL risk
  entry a deviation now adds -- matching a committed golden byte-for-byte on a dedicated fixture
  (`verification/gates/fixtures/auditor_view/`, a four-control catalog over one shared event) whose
  register entry carries a `<script>alert(1)</script>` fragment that every rendering must escape,
  never emit raw. `auditor.json`'s `counts` are checked against a fresh `aggregate()` over the same
  run's own `assertions.json`, never a `report.json` (which does not exist), and the re-run command
  printed in `auditor.md`/`.html` itself is checked to include `--deviations`.

## 0.13.0

- Added `VG-SECURITY-VIEW`: the security view (18.16, Hill 8's security lens: OWASP ASI, MITRE ATLAS,
  and the OWASP Agent Control Standard on the baseline controls) restricts its `standards_citations`
  to exactly `owasp-asi-2026`/`mitre-atlas`/`owasp-acs`, never diverges from the same run's own
  `activity.json` (`actions_by_effect_class`/drift), and matches a committed golden byte-for-byte on a
  dedicated fixture (`verification/gates/fixtures/security_view/`) whose one subject supplies a
  hostile-named (`<script>alert(1)`), undeclared, `effect_class: irreversible` `ToolCall`, a denied
  `AuthzCheck`, and an undeclared `ModelCall`. `--for security` writing `security.json`/`.md`/`.html`
  through a real `assess` run is Python-only today (TypeScript and Java have no `--for`/multi-format
  `write_report` yet, a pre-existing, disclosed scope gap); the cross-engine parity claim instead runs
  the pure `compute_security_view`/`SecurityView.compute` function itself, through each engine's
  test-only `security-view` CLI verb, over a shared `activity_and_assertions.json` payload, and checks
  all three print byte-identical JSON.

## 0.12.0

- Added `VG-PROJECT-TIME`: the installed Python wheel, offline from an empty directory, turns a
  records folder naming two distinct agents (plus one file with no agent id at all) into the
  risk-lead/CIO project view with no `--profile`, so every discovered agent is genuinely undeclared
  (Hill 7). `project.md`/`project.json` list all three discovered subjects, `undeclared_agents` names
  exactly the two real agent ids, and each real agent's own `agents/<dirname>/assertions.json` names
  only that agent's own findings -- all within the five-minute first-report budget counted from the
  start of the install.

## 0.11.0

- Added `VG-PROJECT-VIEW`: the three engines compute the same project view over a dedicated 3-subject
  fixture (`verification/gates/fixtures/project_view/`) -- one subject missing only a ModelCall, the
  other missing both a ModelCall and a ToolCall for the same control, and one of the two subjects'
  events naming a third agent identity no subject declares (Hill 7). Each engine's `project.json` is
  byte-identical to a committed golden, `undeclared_agents` honestly names the undeclared identity,
  `top_gaps` names a real blind spot spanning both fixture subjects, and each engine's rendered
  `project.md` matches its own committed golden. A single-subject run (VG-BLIND-SPOTS' own fixture,
  reused) writes no project-view artifact on any engine, closing the `> 1` vs `>= 1` subject-count
  regression a 3-subject-only fixture could never catch.

## 0.10.0

- Added `VG-OWN-RULES`: an operator installs fresh and, from an empty directory, authors a from-scratch
  catalog (`catalog init`), previews its own support matrix (`catalog lint --support-matrix`), signs it
  with a freshly generated key (`catalog sign --new-key --write-trust-root`), and assesses their own
  evidence bundle against it to a real conformant verdict, within the same five-minute budget as
  `VG-FIRST-REPORT-TIME` (Hill 6); the support matrix's exact rung, owner and adapters are pinned, and a
  catalog tampered after signing is refused at assess time (`input.catalog_unverified`).

## 0.9.0

- Added `VG-RERUN-TIME`: a sender packages and signs a shareable `corpus/quickstart` report bundle
  (`assess --package-for-sharing`, `sign --write-trust-root`); a separate recipient installs the wheel
  fresh and `verify --report`s it offline, timed from before the recipient's own install, reproducing
  every canonical output byte for byte inside a ten-minute budget (Hill 3); a tampered report and a
  tampered evidence file, both checked against an external trust root, are each refused with their own
  exact key (`verify.report_output_tampered`, `verify.report_evidence_tampered`).

## 0.8.0

- Added `VG-AUDIENCE-PRESETS`: `agentce assess --for {engineering,compliance,security,ci,share}` resolves
  to the documented `--emit` set for each audience preset over a dedicated AUD-01 fixture catalog, and the
  `CI` environment variable extends -- never replaces -- the legacy default when neither `--for` nor
  `--emit` is given (a control pair proves the addition, not a silent swap to the minimal `ci` preset).

## 0.7.0

- Added `VG-DIFF`: `agentce diff --format md` renders a classified "## What changed" section
  (closed/opened/other) over the full six-outcome vocabulary, matching a committed golden over a
  dedicated fixture pair that exercises every classification bucket at once.
- Added `VG-GOLDEN-LOOP`: a scripted `assess -> apply the skill's code-level step -> re-assess -> diff`
  loop closes a real blind spot end to end, tied to 18.5's own `blind-spots.json` and the remediation
  package's `expected_transition`, not just to `assess`'s own before/after outcomes.
- Added `VG-SELF-APPROVAL-LABEL`: an approval an agent records about itself stays labelled `self_report`
  end to end through the real CLI; a class-mismatched event claiming `independent_system` is quarantined
  and gains nothing, over two independent honest/spoofed fixture bundles.

## 0.6.1

- Changed `VG-BLIND-SPOTS`: its fixture gained a second control (`NEED-02`) so its two blind spots differ on
  `checks_unlocked` (1 vs 0) rather than tying on every field -- the prior single-control fixture could not
  tell a correct ranking from a missing, inverted, or discovery-order one, since the tied groups' discovery
  order already matched their (accidentally) correct rank order. Two more seeded faults were added: an
  inverted ranking key, and a dropped concrete step for a rung.

## 0.6.0

- Added `VG-BLIND-SPOTS`: the three engines rank the same blind spots over a small, dedicated fixture (not
  the 132-project corpus, where the case does not occur naturally) whose one control is missing two distinct
  requirements at once (the `needed_by` case) -- neither is wrongly claimed as unlocked, and each has a rung,
  an owner, and a concrete step consistent with the evidence ladder. Each engine's `blind-spots.json` is
  compared byte-for-byte against a committed golden (`blind_spots_golden.json`), so a computation bug shared
  by all three engines cannot hide behind cross-engine agreement alone.

## 0.5.0

- Added `VG-WHAT-THEY-DID`: the three engines write an `activity.json` counting the agents, models, tools,
  actions by effect class, approvals by who recorded them, and actions denied or blocked a run's records
  show, and a tool or model the events show that no subject declares honestly surfaces as undeclared. Each
  engine's output is compared byte-for-byte against a committed golden (`what_they_did_golden.json`), so a
  computation bug shared by all three engines cannot hide behind cross-engine agreement alone.

## 0.4.0

- Added `VG-LOCKED-COPY`: the website builds, and the built landing page and the README carry the approved
  tagline, headline and subline word for word, with the wording they replaced gone.

## 0.3.0

- Added `VG-FIRST-REPORT-TIME`: an installed package turns a folder of OpenTelemetry GenAI or OpenInference
  records into a report with `agentce assess <folder>` and no other argument, offline, within five minutes of the
  start of the install; files it cannot read are listed with a reason and a folder with no recognised record is
  refused before anything is written.

## 0.2.0

- Added `VG-BASELINE-LENS`: the baseline catalog cites at least two standards per control by clause reference
  (nothing the standards crosswalk files do not already map), is the default lens of an assessment that names no
  catalog, and every report lists the lenses a run can choose.

## 0.1.0

- Added the runner `verification/run` with `--quick`, `--full`, `--gate`, `--demo-fault`, `--lint-registry`,
  `--list` and `--json`, the gate registry `gates.json` and the results schema `results.schema.json`.
- Added `VG-SUITE-SELFTEST`: the runner reports pass, fail, skipped and timeout honestly, and the registry lint
  rejects a gate with no seeded fault, no claim or invariant, a malformed id, or a fault that does not apply.
- Added `VG-ATTESTATION-SIGNATURES`: an attestation is verified only after its DSSE signature verifies against
  a trusted key.
