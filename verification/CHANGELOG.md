# Verification suite changelog

Every change to a build gate (added, changed or retired) is recorded here, newest first. The registry lint
fails if a gate in `gates.json` is not named in this file.

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
