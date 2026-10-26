/**
 * The buyer view (18.18): generated questionnaire answers, a one-page summary, and how to check the
 * report.
 *
 * A faithful port of the Python reference (`agentce/buyer_view.py`). `computeBuyerView` adds no new
 * rollup and re-derives no verdict (SPEC §9.2): a CAIQ or AI Controls Matrix question is answered by
 * selecting the `Assertion.crosswalk` entries whose `framework` is one of the two questionnaire
 * frameworks. A question with no mapped control is absent from `answers`. Field names, key order and
 * sort order match Python's so `buyer.json` is byte-identical across engines.
 */

import { type Assertion, aggregate, evidencePointerToJson } from "./assertions";
import { MANUAL_MODES } from "./auditorView";
import type { BlindSpots, CheckRef } from "./blindSpots";
import { catalogue } from "./messages";
import { pyTruthy } from "./readiness";
import { byteCompare, pyStr } from "./util";

/** The two questionnaire frameworks this view answers, in fixed render order. */
export const BUYER_FRAMEWORKS = ["caiq", "ai-controls-matrix"] as const;

/** Each framework's own `version:` field, copied from its crosswalk file (buyer_view.py's
 * `BUYER_QUESTIONNAIRE_VERSIONS`). */
export const BUYER_QUESTIONNAIRE_VERSIONS: Record<string, string> = {
  caiq: "4.0.2",
  "ai-controls-matrix": "1.1",
};

/** Each framework's display title for the "evidence against {title} {version}" heading. */
export const BUYER_QUESTIONNAIRE_TITLES: Record<string, string> = {
  caiq: "the CAIQ",
  "ai-controls-matrix": "the AI Controls Matrix",
};

export interface BuyerMissing {
  event: string;
  class: string;
  ladder_rung: number;
  owner_key: string;
  step_kind: string;
}

export type BuyerGapStep =
  | { kind: "blind_spot"; missing: BuyerMissing[] }
  | { kind: "no_population" }
  | { kind: "coverage_unknown" };

export interface BuyerAnswer {
  framework: string;
  question: string;
  control: string;
  control_version: string;
  subject: string;
  outcome: string;
  mode: string;
  verified: boolean;
  evidence: Record<string, unknown>[];
  gap_step: BuyerGapStep | null;
  manual_checklist_note?: string;
}

export interface BuyerView {
  answers: BuyerAnswer[];
  by_question: Record<string, Record<string, number[]>>;
  counts: Record<string, number>;
}

/** A check-ref's (subject, control, control_version) triple as one map key (buyer_view.py's
 * `_ref_key`; `catalog` is not part of it, the limitation Python discloses). */
function refKey(subject: string, control: string, controlVersion: string): string {
  return JSON.stringify([subject, control, controlVersion]);
}

/** The triple -> gap-step index, built once per run (buyer_view.py's `_buyer_gap_step_index`): a
 * blind spot's missing requirements first, then `no_population` and `coverage_unknown` where no
 * earlier bucket matched. */
function buyerGapStepIndex(blindSpots: BlindSpots): Map<string, BuyerGapStep> {
  const key = (ref: CheckRef): string => refKey(ref.subject, ref.control, ref.control_version);
  const missingByKey = new Map<string, BuyerMissing[]>();
  for (const bs of blindSpots.blind_spots ?? []) {
    const entry: BuyerMissing = {
      event: bs.event,
      class: bs.class,
      ladder_rung: bs.ladder_rung,
      owner_key: bs.owner_key,
      step_kind: bs.step_kind,
    };
    for (const ref of [...bs.unlocked_checks, ...bs.needed_by_checks]) {
      const list = missingByKey.get(key(ref)) ?? [];
      missingByKey.set(key(ref), list);
      list.push(entry);
    }
  }
  const index = new Map<string, BuyerGapStep>();
  for (const [k, missing] of missingByKey) {
    index.set(k, { kind: "blind_spot", missing });
  }
  for (const [bucket, kind] of [
    [blindSpots.no_population ?? [], "no_population"],
    [blindSpots.coverage_unknown ?? [], "coverage_unknown"],
  ] as const) {
    for (const ref of bucket) {
      if (!index.has(key(ref))) {
        index.set(key(ref), { kind });
      }
    }
  }
  return index;
}

/** The questionnaire-answer selection of `assertions` for a whole run (buyer_view.py's
 * `compute_buyer_view`): `answers` sorted by (framework, question, control, control_version,
 * subject, outcome) in code-point order, `by_question` one map per framework. */
export function computeBuyerView(
  assertions: Assertion[],
  blindSpots: BlindSpots,
  counts?: Record<string, number>,
): BuyerView {
  const note = catalogue()["report.manual_checklist_not_yet_evaluated"] as string;
  const gapStepIndex = buyerGapStepIndex(blindSpots);
  const answers: BuyerAnswer[] = [];
  for (const a of assertions) {
    for (const xw of a.crosswalk) {
      const framework = pyStr(xw.framework ?? "");
      if (!(BUYER_FRAMEWORKS as readonly string[]).includes(framework)) {
        continue;
      }
      const entry: BuyerAnswer = {
        framework,
        question: pyStr(xw.clause ?? ""),
        control: a.control,
        control_version: a.controlVersion,
        subject: a.subject,
        outcome: a.outcome,
        mode: a.mode,
        verified: pyTruthy(xw.verified),
        evidence: a.evidence.map(evidencePointerToJson),
        gap_step:
          a.outcome === "insufficient_evidence"
            ? (gapStepIndex.get(refKey(a.subject, a.control, a.controlVersion)) ?? null)
            : null,
      };
      if (MANUAL_MODES.has(a.mode) && a.outcome === "not_assessed") {
        entry.manual_checklist_note = note;
      }
      answers.push(entry);
    }
  }
  const sortKey = (e: BuyerAnswer): string[] => [
    e.framework,
    e.question,
    e.control,
    e.control_version,
    e.subject,
    e.outcome,
  ];
  answers.sort((x, y) => {
    const kx = sortKey(x);
    const ky = sortKey(y);
    for (let i = 0; i < kx.length; i++) {
      const c = byteCompare(kx[i] as string, ky[i] as string);
      if (c !== 0) return c;
    }
    return 0;
  });
  const byQuestion: Record<string, Record<string, number[]>> = {};
  for (const fw of BUYER_FRAMEWORKS) {
    byQuestion[fw] = {};
  }
  answers.forEach((entry, index) => {
    const questions = byQuestion[entry.framework] as Record<string, number[]>;
    if (!Object.hasOwn(questions, entry.question)) {
      // defineProperty, not assignment: a question named `__proto__` stays an own key.
      Object.defineProperty(questions, entry.question, {
        value: [],
        enumerable: true,
        writable: true,
        configurable: true,
      });
    }
    (questions[entry.question] as number[]).push(index);
  });
  return { answers, by_question: byQuestion, counts: counts ?? aggregate(assertions) };
}
