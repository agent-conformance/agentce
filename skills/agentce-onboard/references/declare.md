# Phase A — Declare

The procedure for the declaration phase of `agentce-onboard` (SPEC §13.3.3 Phase A). Produces
`agentce/applicability.yaml`, `agentce/domain.linkml.yaml`, `agentce/reference/`, and an empty
`agentce/deviations.yaml` (template only — never populated by this skill, S-3).

1. **Subjects.** One subject per deployed agent *system* (not per instance). Assign the SPIFFE ID or
   `agentce:subject/<id>` used at runtime; record `role` (`deployer`, `provider`, or `both`).
2. **Intended purpose and affected parties.** Draft each from existing documentation. Where none
   exists, record the gap in `docs/agentce/gaps.md` rather than inventing text.
3. **Annex III classification.** Use `references/annex-iii-classification.md`. Output
   `annex_iii_category` and `annex_iii_rationale` with `classification_status: draft_for_legal_review`.
   This skill never sets `confirmed`; a named reviewer does (S-8).
4. **Decision types.** Derive candidates from the code (functions or nodes that return
   recommendations, classifications, status changes, messages to people, or records written) and from
   process documents. Name them as IRIs under the organisation's namespace. Propose which are
   consequential using the Art. 86 threshold (legal or similarly significant effect on a natural
   person); propose `affects_natural_person` and data classes per type.
5. **Domain binding.** Draft `agentce/domain.linkml.yaml` from `assets/domain.template.linkml.yaml`:
   subclasses of `agentce:Decision` and `agentce:ContextItem`, `consequential`,
   `required_oversight_modality`, `data_classes`. Run `linkml_validate --profile` on the profile and
   validate any sample events with `linkml_validate --events`.
6. **Reference snapshots** (`agentce/reference/`): policy versions with digests, declared oversight
   measures and instructions-for-use extracts (Art. 13), the component inventory with digests (from
   `BundleLoaded` sources or an AIBOM scan), registration references if any, and source manifests
   (expected record counts per window, where the source provides them).
7. **Profile.** Write `agentce/applicability.yaml` from `assets/applicability.template.yaml` and run
   `lint_profile --profile agentce/applicability.yaml` until clean. The lint enforces: schema validity;
   a trust class and justification per source (and a coverage manifest, or `manifest_reason`, for
   enforcement points); observation window ≥ 90 days or `pilot_window: true`; `confirmed_by` for
   classification, decision typing, and oversight; and, for the Conduct overlay, an effect scope per
   task type.
8. **Conduct overlay (optional, SPEC §7.7).** If targeted, declare task effect scopes from
   `assets/task-scope.template.yaml` — per task type the allowed tools and effect classes, resources
   and data classes, egress destinations, budgets, and the effect classes that require human approval —
   plus the authorised principal roles. `lint_profile` refuses the overlay without a scope for every
   declared task type.
