# Verification suite changelog

Every change to a build gate (added, changed or retired) is recorded here, newest first. The registry lint
fails if a gate in `gates.json` is not named in this file.

## 0.65.0

- `VG-CLI-OPTIONS` (18.107) grows from 45 to 77 rows and from nine to eleven seeded faults. Python
  answered most mistyped command lines with argparse's usage text and no message key, read an
  abbreviation such as `--bun x` as `--bundle x`, and took `-hx` as `-h`, printing the usage and
  dropping the `x`. Every Python argv error now exits 3 with a key, the one TypeScript and Java give where
  they give one; every Python parser refuses an abbreviation and a clustered short option. The new rows
  cover `-hx` and `-hh` on six commands, a `-h` cluster after a real target, unknown flags, value-less
  flags, a bad choice and an unknown action; where TypeScript or Java still answer another way, the row's
  override names the item that changes it (18.108 to 18.111). `VG-FAIL-ON-PARITY` and
  `VG-DEVIATIONS-PARITY` now expect Python's key for a value-less flag. The gate's helper skips a build
  whose inputs hash the same as at its last build, and a red run stops at its first failure, so the
  seeded-fault demo stays inside its CI lane.

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
