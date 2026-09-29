/**
 * Message-key catalogue for report rendering (SPEC §9.3, §8.4).
 *
 * All human-readable text in `report.md` and `report.html` comes from message keys, so a translation
 * changes only the report -- never `assertions.json`, the manifest digests, or the claim. v1 ships the
 * `en` catalogue; a partial `de` catalogue demonstrates the translation mechanism (any key it omits
 * falls back to `en`). The report language is recorded in the manifest as `run.report_language` and has
 * no effect on the machine-readable outputs. Mirrors `engines/python/agentce/messages.py` and
 * `engines/java/.../Messages.java`.
 */

export const DEFAULT_LANGUAGE = "en";

const EN: Record<string, string> = {
  "report.title": "AgentCE conformance report",
  "report.summary_heading": "Outcome summary",
  "report.assertions_heading": "Assertions",
  "report.no_controls": "No controls were evaluated.",
  "report.activity_heading": "What your agents did",
  "report.activity_agents_label": "Agents",
  "report.activity_models_label": "Models",
  "report.activity_tools_label": "Tools",
  "report.activity_actions_label": "Actions by effect class",
  "report.activity_approvals_label": "Approvals recorded by",
  "report.activity_denied_label": "Denied or blocked",
  "report.activity_none_agents": "no agent identity found in the records",
  "report.activity_none_undeclared": "every tool and model your agents used is declared",
  "report.activity_undeclared_heading": "Not yet declared in your profile",
  "report.activity_undeclared_tools_label": "Tools",
  "report.activity_undeclared_models_label": "Models",
  "report.activity_recorder_enforcement_point": "a system that could have stopped it",
  "report.activity_recorder_independent_system": "a system the agent cannot edit",
  "report.activity_recorder_self_report": "the agent's own account",
  "report.activity_denied_approval_rejected": "a human rejected it",
  "report.activity_denied_authz_denied": "blocked by an authorization check",
  "report.activity_denied_policy_denied": "blocked by policy",
  "report.activity_denied_refused": "the agent refused",
  "report.verdict_heading": "Verdict",
  "report.top_gaps_heading": "Top gaps",
  "report.no_gaps": "none",
  "report.next_step_heading": "Next step",
  "report.gaps_more": "{n, plural, one {+# more gap} other {+# more gaps}}",
  "report.project_title": "AgentCE project view",
  "report.project_heading": "Agents in this project",
  "report.project_agent_column": "Agent",
  "report.project_declared_column": "Declared",
  "report.project_verdict_column": "Verdict",
  "report.project_what_it_did_column": "What it did",
  "report.project_what_it_did_cell": "agents: {agents}; actions: {actions}",
  "report.project_declared_badge": "Declared",
  "report.project_undeclared_badge": "Undeclared",
  "report.project_top_gaps_heading": "Top gaps across agents",
  "report.project_top_gap_agents": "Agents: {agents}.",
  "report.project_undeclared_heading": "Undeclared agents",
  "report.project_agent_report_link": "Full report",
  "verdict.conformant": "Conformant — every applicable control met its expectations with evidence.",
  "verdict.incomplete":
    "Incomplete — no control failed, but not every applicable control is demonstrated.",
  "verdict.non-conformant": "Non-conformant — at least one applicable control failed.",
  "next.conformant":
    "No gaps. Run the assessment again when the agent, its evidence, or the catalog changes.",
  "next.incomplete":
    "Supply the missing evidence, or complete the manual checks, for the controls listed under Top gaps, then run the assessment again.",
  "next.non-conformant":
    "Fix the non-conformant controls listed under Top gaps, then run the assessment again.",
  "outcome.conformant": "conformant",
  "outcome.insufficient_evidence": "insufficient evidence",
  "outcome.non-conformant": "non-conformant",
  "outcome.not_applicable": "not applicable",
  "outcome.not_assessed": "not assessed",
  "outcome.partial": "partial",
};

/** A partial translation, to exercise the mechanism; missing keys fall back to `en`. */
const DE: Record<string, string> = {
  "report.title": "AgentCE-Konformitätsbericht",
  "report.summary_heading": "Ergebnisübersicht",
  "report.assertions_heading": "Aussagen",
  "report.no_controls": "Es wurden keine Kontrollen bewertet.",
};

const CATALOGUES: Record<string, Record<string, string>> = { en: EN, de: DE };

/** The message catalogue for `language`, backed by `en` for any missing key. */
export function catalogue(language: string = DEFAULT_LANGUAGE): Record<string, string> {
  return { ...EN, ...CATALOGUES[language] };
}
