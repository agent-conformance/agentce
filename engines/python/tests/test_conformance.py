"""The Engine Conformance Suite runner (SPEC §11.5): claim, project counts, and no_ml folding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agentce.conformance import run_ecs
from agentce.errors import InputError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENGINE = _REPO_ROOT / "engines" / "python"

SUBJECT = "spiffe://corp/agents/a"


def _decision_event() -> dict[str, object]:
    return {
        "specversion": "1.0",
        "id": "dec-1",
        "source": "urn:agentce:source:agent",
        "type": "org.agent-conformance.evidence.Decision.v1",
        "time": "2026-05-01T09:00:00.000Z",
        "subject": SUBJECT,
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": "self_report",
        "data": {
            "@type": "Decision",
            "decision_type": "dom:CreditDecision",
            "agent": {"id": SUBJECT},
        },
    }


def _write_project(root: Path, project_id: str, *, with_evidence: bool = True) -> None:
    proj = root / "projects" / project_id
    if with_evidence:
        events_dir = proj / "evidence" / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        events_file = events_dir / "stream.jsonl"
        events_file.write_text(json.dumps(_decision_event()) + "\n", encoding="utf-8")
        manifest = {
            "agentce_bundle_version": 1,
            "files": [
                {
                    "path": "events/stream.jsonl",
                    "sha256": hashlib.sha256(events_file.read_bytes()).hexdigest(),
                }
            ],
        }
        (proj / "evidence" / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    else:
        proj.mkdir(parents=True, exist_ok=True)
    (proj / "applicability.yaml").write_text(
        f'subjects:\n  - id: "{SUBJECT}"\n    role: "both"\ncatalogs:\n  - "eu-ai-act@2026.09"\n',
        encoding="utf-8",
    )
    (proj / "domain.linkml.yaml").write_text(
        'decision_types:\n  - id: "dom:CreditDecision"\n'
        '    subclass_of: "agentce:ConsequentialDecision"\n    consequential: true\n',
        encoding="utf-8",
    )


def _write_corpus(
    root: Path, project_ids: list[str], *, broken: set[str] | None = None
) -> None:
    broken = broken or set()
    root.mkdir(parents=True, exist_ok=True)
    for pid in project_ids:
        _write_project(root, pid, with_evidence=pid not in broken)
    manifest = {
        "corpus_version": "test",
        "projects": [{"id": pid, "events": 1} for pid in project_ids],
    }
    (root / "corpus-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_claim_full_on_a_clean_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, ["credit/x/known-pass", "credit/y/known-pass"])
    report = run_ecs(engine_path=_ENGINE, corpus_dir=corpus, out_dir=tmp_path / "out")
    assert report["claim"] == "full"
    assert report["no_ml"] == "pass"
    assert report["projects"] == {"total": 2, "identical": 2, "different": 0}
    assert (tmp_path / "out" / "implementation-report.json").is_file()
    # Each project produced a report with a manifest, ready for the determinism comparison.
    assert (
        tmp_path / "out" / "projects" / "credit/x/known-pass" / "manifest.json"
    ).is_file()


def test_a_broken_project_makes_the_claim_partial(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(
        corpus,
        ["credit/x/known-pass", "credit/z/broken"],
        broken={"credit/z/broken"},
    )
    report = run_ecs(engine_path=_ENGINE, corpus_dir=corpus, out_dir=None)
    assert report["projects"]["identical"] == 1
    assert report["projects"]["different"] == 1
    assert report["claim"] == "partial"
    assert report["failures"] and report["failures"][0]["project"] == "credit/z/broken"


def test_empty_corpus_is_not_full(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [])
    report = run_ecs(engine_path=_ENGINE, corpus_dir=corpus, out_dir=None)
    assert report["claim"] == "none"
    assert report["projects"]["total"] == 0


def test_missing_corpus_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        run_ecs(engine_path=_ENGINE, corpus_dir=tmp_path / "nope", out_dir=None)


def test_a_corpus_source_tree_is_generated(tmp_path: Path) -> None:
    # A directory with a generator but no manifest is materialised by running the generator.
    corpus = tmp_path / "corpus"
    gen_dir = corpus / "generator"
    gen_dir.mkdir(parents=True)
    # A stand-in generator that writes a one-project corpus, exercising the materialise path.
    (gen_dir / "generate.py").write_text(
        "import argparse, json, hashlib\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser(); p.add_argument('--set'); p.add_argument('--out', required=True)\n"
        "a = p.parse_args()\n"
        "out = Path(a.out); ev = out / 'projects' / 'credit/x/known-pass' / 'evidence'\n"
        "(ev / 'events').mkdir(parents=True)\n"
        "line = json.dumps({'specversion':'1.0','id':'d1','source':'urn:s','type':'org.agent-conformance.evidence.Decision.v1','time':'2026-05-01T09:00:00.000Z','subject':'"
        + SUBJECT
        + "','datacontenttype':'application/ld+json','agentcesourceclass':'self_report','data':{'@type':'Decision','decision_type':'dom:CreditDecision','agent':{'id':'"
        + SUBJECT
        + "'}}})\n"
        "f = ev / 'events' / 's.jsonl'; f.write_text(line + '\\n')\n"
        "(ev / 'manifest.json').write_text(json.dumps({'agentce_bundle_version':1,'files':[{'path':'events/s.jsonl','sha256':hashlib.sha256(f.read_bytes()).hexdigest()}]}))\n"
        "proj = out / 'projects' / 'credit/x/known-pass'\n"
        "(proj / 'applicability.yaml').write_text('subjects:\\n  - id: \""
        + SUBJECT
        + '"\\n    role: "both"\\n\')\n'
        "(proj / 'domain.linkml.yaml').write_text('decision_types:\\n  - id: \"dom:CreditDecision\"\\n    subclass_of: \"agentce:ConsequentialDecision\"\\n    consequential: true\\n')\n"
        "(out / 'corpus-manifest.json').write_text(json.dumps({'corpus_version':'gen','projects':[{'id':'credit/x/known-pass','events':1}]}))\n",
        encoding="utf-8",
    )
    report = run_ecs(engine_path=_ENGINE, corpus_dir=corpus, out_dir=None)
    assert report["projects"]["total"] == 1
    assert report["claim"] == "full"
