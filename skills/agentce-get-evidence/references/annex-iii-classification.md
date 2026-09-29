# Annex III classification (draft for legal review)

A rule table keyed to the EU AI Act Annex III high-risk points, used in Phase A to **draft** a
subject's classification. The skill only ever produces a draft: it sets
`classification_status: draft_for_legal_review` and records the reasoning; a named reviewer confirms
(rule S-8). The skill never sets `classification_status: confirmed`.

For each subject, work through the points and record the first that applies, with the clause quoted in
`annex_iii_rationale`:

| Annex III point | Area (paraphrase — quote the clause in the rationale) | Typical agent examples |
|---|---|---|
| 1 | Biometrics (categorisation, emotion recognition) | face/voice categorisers |
| 2 | Critical infrastructure (safety components) | grid/traffic/water control |
| 3 | Education and vocational training (access, evaluation) | admissions, exam scoring |
| 4 | Employment, workers management, self-employment access | CV screening, task allocation |
| 5 | Access to essential private and public services and benefits | **credit scoring**, benefits eligibility, emergency dispatch |
| 6 | Law enforcement | risk assessment, evidence evaluation |
| 7 | Migration, asylum, border control | visa/asylum risk |
| 8 | Administration of justice and democratic processes | judicial research, influencing votes |

Output fields:

- `annex_iii_category`: the point number and short label, or `not_annex_iii` with the reasoning.
- `annex_iii_rationale`: the quoted clause and why the subject's decisions fall in (or out of) scope.
- `classification_status`: always `draft_for_legal_review` from this skill.
- `affects_natural_person` and the consequential-decision flags per decision type (Art. 86 threshold).

When a subject is Annex III high-risk, it needs a registration reference or a gap entry, and its
consequential decisions require the declared oversight modality — both are checked by `lint_profile`.
