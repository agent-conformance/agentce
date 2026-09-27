# Verification suite changelog

Every change to a build gate (added, changed or retired) is recorded here, newest first. The registry lint
fails if a gate in `gates.json` is not named in this file.

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
