# Governance

How the Agent Conformance Engine is governed (SPEC §14.2). The project is deterministic and
vendor-neutral; these documents fix the change process and the policies a contributor or adopter
relies on, and record which parts of that structure exist today and which are planned.

## Current state

The project has one maintainer. No working group has been chartered, and no group or committee reviews
changes yet. Every decision described below is taken by that maintainer today; where a section says
"planned", it describes the structure the project intends to grow into, not one that exists.

## Working groups (planned, not yet chartered)

The areas below are planned working groups. None exists yet: each becomes a working group only when it
has a charter and more than one member, which requires people the project does not yet have.

| Planned group | Would own |
|---|---|
| Catalog | Rules, crosswalks, and interpretations. Would include legal reviewers for clause mappings. |
| Engine | The portable shape profile, canonical form, numerics, and the Engine Conformance Suite. |
| Adapters | Source adapters and their support matrices. |
| Corpus | The simulated corpus, its generator, and the golden outputs. |

## Change process

- **RFC process.** Changes to the evidence model, the portable shape profile, the rule format, the
  claim schema, and the outcome vocabulary go through an RFC, decided by the maintainer today. An
  interpretation-register entry is planned to additionally require legal review in a Catalog working
  group, which does not yet exist; until it does, no entry claims that review has happened.
- **DCO sign-off.** Every commit carries a Developer Certificate of Origin sign-off (`git commit -s`).
  There is no CLA at launch; inbound licence equals outbound (Apache-2.0 for executable artifacts and
  catalogs, CC-BY-4.0 for documentation and generated datasets).
- **Neutrality rule.** No maintainer may add a control that can be satisfied only by one vendor's
  product. Techniques may name products; rules may not.
- **Release blockers.** The precision gate (SPEC §11.6) and the Engine Conformance Suite gate are
  release blockers; a change that regresses either does not ship.

## Policies

| Document | Covers |
|---|---|
| [Versioning](VERSIONING.md) | How the model, catalogs, engines, and datasets are versioned and how compatibility is signalled. |
| [Support and deprecation](SUPPORT.md) | How long a catalog version and an engine major version are supported, and how deprecations are announced. |
| [Security process](SECURITY-PROCESS.md) | Private disclosure, response timelines, CVEs, and the OpenVEX statements published with every release. |
| [Conformance program](CONFORMANCE-PROGRAM.md) | What a conformance claim is, how the implementation-report registry accepts a report, the namespace rule for forks, and attribution. |

## Attribution

The [`NOTICE`](../NOTICE) file names the project and the authors of the catalogs, corpus, and
techniques; Apache-2.0 §4(d) obliges every derivative to preserve it. Catalog files additionally carry
a `provenance` block that `agentce catalog lint --require-provenance` enforces (SPEC §14.5 CP-3).

## Pre-GA

The pre-GA guardrails (SPEC front matter, G-1…G-8) apply to every contributor until the maintainer
explicitly requests the general-availability update. They constrain visibility, not engineering
progress.
