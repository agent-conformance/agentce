# Verification suite changelog

Every change to a build gate (added, changed or retired) is recorded here, newest first. The registry lint
fails if a gate in `gates.json` is not named in this file.

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
