"""Generate tools/fixtures/report_formats/buyer_cases.json: buyer views (compute_buyer_view's shape) plus the
renderer context (packaged, folder, argv, catalogs), each in en and de (18.16h)."""
import json, sys

HOSTILE = ["`tick`", "<script>alert(1)</script>", "[x](http://e)", "a &amp; b \"q\" 'q'", "*_#|\\",
           "line\r\nbreak", "nul\x00bel\x07esc\x1bdel\x7f", "bidi‮ltr​zw", "u v w",
           "x" * 260, "sup\U0001F600\U00010000", "", "   ", "{control}", "{folder}"]
OUTCOMES = ["conformant", "non-conformant", "partial", "not_applicable", "not_assessed", "insufficient_evidence"]
NOTE = "recording this control's checklist answer as a live outcome is not built yet (SPEC §13.3.4 Stage 3); it stays not_assessed until then. See agentce-prepare-to-share's checklist procedure for the next step."


def answer(fw, q, control="C-1", outcome="conformant", verified=False, evidence=(), gap=None, note=False,
           version="2026.09", subject="spiffe://corp/agents/a", mode="automated"):
    e = {"framework": fw, "question": q, "control": control, "control_version": version, "subject": subject,
         "outcome": outcome, "mode": mode, "verified": verified,
         "evidence": [{"ref": r, "kind": "event"} for r in evidence], "gap_step": gap}
    if note:
        e["manual_checklist_note"] = NOTE
    return e


def view(answers, counts=None):
    answers = sorted(answers, key=lambda e: (e["framework"], e["question"], e["control"], e["control_version"],
                                             e["subject"], e["outcome"]))
    by_q = {"caiq": {}, "ai-controls-matrix": {}}
    for i, e in enumerate(answers):
        by_q.setdefault(e["framework"], {}).setdefault(e["question"], []).append(i)
    return {"answers": answers, "by_question": by_q,
            "counts": counts if counts is not None else {o: 0 for o in OUTCOMES}}


miss = lambda ev, cls, owner, step: {"event": ev, "class": cls, "ladder_rung": 2, "owner_key": owner, "step_kind": step}
CTX = {"packaged": False, "folder": "out", "argv": ["assess", "--bundle", "b", "--profile", "p.yaml"],
       "catalogs": [["eu-ai-act", "2026.09"]]}
base = [
    ("empty", view([]), {}),
    ("empty_no_argv", view([]), {"argv": [], "catalogs": []}),
    ("every_outcome", view([answer("caiq", f"IAM-{i}", f"C-{i}", o, verified=i % 2 == 0, evidence=[f"agentce:event/e{i}"] if i % 3 else [])
                            for i, o in enumerate(OUTCOMES)],
                           {"conformant": 3, "non-conformant": 2, "partial": 0, "not_applicable": 1, "not_assessed": 1, "insufficient_evidence": 1}), {}),
    ("counts_extra_key", view([], {"conformant": 1, "bogus": 9}), {}),
    ("gap_steps", view([
        answer("caiq", "LOG-1", "G-1", "insufficient_evidence", gap=None),
        answer("caiq", "LOG-2", "G-2", "insufficient_evidence", gap={"kind": "no_population"}),
        answer("caiq", "LOG-3", "G-3", "insufficient_evidence", gap={"kind": "coverage_unknown"}),
        answer("caiq", "LOG-4", "G-4", "insufficient_evidence", gap={"kind": "blind_spot", "missing": [
            miss("Decision", "enforcement_point", "platform_or_security", "request"),
            miss("ToolCall", "traces", "agent_team", "code_change"),
            miss("Approval", "independent_system", "ticketing_or_iam", "request")]}),
    ]), {}),
    ("manual_note", view([answer("caiq", "IAM-9", "M-1", "not_assessed", note=True, mode="manual")]), {}),
    ("verified_shapes", view([answer("caiq", "V-1", "V-1", verified=v) for v in (True, False)]), {}),
    ("several_per_question", view([answer("caiq", "IAM-1", c, o, subject=s) for c in ("B", "A") for o in ("partial", "conformant")
                                   for s in ("spiffe://z", "spiffe://a")]), {}),
    ("acm_answers", view([answer("ai-controls-matrix", "AIS-01", "X-1"), answer("caiq", "IAM-1", "X-1")]), {}),
    ("packaged", view([answer("caiq", "IAM-1")]), {"packaged": True, "folder": "./shared report"}),
    ("packaged_hostile_folder", view([]), {"packaged": True, "folder": "a`b<c>&d{e}`"}),
    ("packaged_empty_folder", view([]), {"packaged": True, "folder": ""}),
    ("hostile_argv_catalogs", view([]), {"argv": ["assess", "--bundle", "`x`<y>&", "{folder}"],
                                         "catalogs": [["<cat>", "1`2"], ["a&b", ""]]}),
]
for i, h in enumerate(HOSTILE):
    base.append((f"hostile_{i}", view([
        answer("caiq", h, control=h, version=h, subject=h, outcome="insufficient_evidence", evidence=[h, "ok"],
               gap={"kind": "blind_spot", "missing": [miss(h, h, "agent_team", "request")]}),
        answer("caiq", h, control="ctl", outcome="conformant", evidence=[h])]), {}))
cases = []
for name, v, ctx in base:
    for lang in ("en", "de"):
        cases.append({"name": f"{name}_{lang}", "language": lang, "view": v, **{**CTX, **ctx}})
json.dump({"cases": cases}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(cases), "cases")
