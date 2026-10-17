---
title: Runtime and Continuous Conformance
description: Re-run assess over an accumulating evidence stream, detect outcome drift, and see the incident that caused it.
---

An assessment is a judgment about a fixed evidence bundle, over a fixed window. An adopter who wants
ongoing assurance — "prove this agent is still conformant next week, and tell me the moment it stops
being" — re-runs `agentce assess` against the same [state directory](/docs/running-assessments/) as new
evidence arrives. This is the engine's model of "continuous": a chronological series of real, point-in-time
`assess` runs over an accumulating stream, not a persistent watch or daemon process. A user, a scheduler,
or a CI job supplies the cadence; the engine supplies the state, the drift detection, and the report. This
is how the engine discharges a requirement that assessment be continuous rather than a one-time snapshot
(the EU AI Act's Art. 72 post-market-monitoring duty is one example).

Each example below is self-contained and runs from a fresh directory. Each record carries the `integrity`
member its exporter writes, which holds the record's own hash and the hash of the record before it in the
same stream. The engine counts an independent-system record at its class only on a stream whose chain
verifies. Without the member, `INC-02` reads insufficient evidence rather than conformant (SPEC §8.1).

An incident register is not an independent count of the agent's calls, so these subjects' coverage is
unknown. Their INT and REC checks read insufficient evidence and every `assess` run below exits 2
(SPEC §6.5). Drift is still tracked for each check, and `INC-02` is the one these examples follow.

## Re-running over an unchanged bundle is a no-op

The state directory (`--state`) remembers each `(subject, control)` pair's most recently recorded
outcome. Re-running the identical evidence bundle changes nothing, so it reports no drift:

```bash
mkdir -p rc/events rc/state
cat > rc/events/incidents.jsonl <<'EOF'
{"specversion":"1.0","id":"inc1","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:00:00Z","subject":"spiffe://example/agents/a","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:00:00Z","reported_at":"2026-05-02T09:00:00Z","agent":{"id":"spiffe://example/agents/a"},"integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"0000000000000000000000000000000000000000000000000000000000000000","hash":"49c190a47f041498ea688dd5d586694eddc28a5947ba9fd77daa6bf128dd69eb"}}}
EOF
DIGEST=$(shasum -a 256 rc/events/incidents.jsonl | cut -d' ' -f1)
cat > rc/manifest.json <<EOF
{"agentce_bundle_version": 1, "domain": "runtime-conformance-example", "sources": [{"id": "urn:example:incidents", "class": "independent_system"}], "files": [{"path": "events/incidents.jsonl", "sha256": "$DIGEST"}]}
EOF
cat > rc/profile.yaml <<'EOF'
profile_version: 1
observation_window:
  start: "2026-05-01T00:00:00Z"
  end: "2026-08-29T00:00:00Z"
catalogs:
  - "eu-ai-act@2026.09"
subjects:
  - id: "spiffe://example/agents/a"
    role: "both"
    evidence_sources:
      - adapter: "register"
        source: "urn:example:incidents"
        class: "independent_system"
EOF
uv run --project engines/python agentce assess --bundle rc --profile rc/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc/out-1 --state rc/state --json > rc/out-1.json || true
python3 -c "import json,sys; print(json.load(open('rc/out-1.json'))['exit_code'])"
uv run --project engines/python agentce assess --bundle rc --profile rc/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc/out-2 --state rc/state --json > /dev/null || true
test ! -f rc/out-2/runtime_drift.jsonl && echo "no drift on the identical re-run"
```

A brand-new state directory — or one written before this feature existed — behaves the same way on its
first tracked run for a pair: there is nothing yet to compare against, so it reports no drift. That is a
normal result, not an error:

```bash
mkdir -p rc0/events rc0/state
cat > rc0/events/incidents.jsonl <<'EOF'
{"specversion":"1.0","id":"inc1","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:00:00Z","subject":"spiffe://example/agents/a","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:00:00Z","reported_at":"2026-05-02T09:00:00Z","agent":{"id":"spiffe://example/agents/a"},"integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"0000000000000000000000000000000000000000000000000000000000000000","hash":"49c190a47f041498ea688dd5d586694eddc28a5947ba9fd77daa6bf128dd69eb"}}}
EOF
DIGEST=$(shasum -a 256 rc0/events/incidents.jsonl | cut -d' ' -f1)
cat > rc0/manifest.json <<EOF
{"agentce_bundle_version": 1, "domain": "runtime-conformance-example", "sources": [{"id": "urn:example:incidents", "class": "independent_system"}], "files": [{"path": "events/incidents.jsonl", "sha256": "$DIGEST"}]}
EOF
cat > rc0/profile.yaml <<'EOF'
profile_version: 1
observation_window:
  start: "2026-05-01T00:00:00Z"
  end: "2026-08-29T00:00:00Z"
catalogs:
  - "eu-ai-act@2026.09"
subjects:
  - id: "spiffe://example/agents/a"
    role: "both"
    evidence_sources:
      - adapter: "register"
        source: "urn:example:incidents"
        class: "independent_system"
EOF
uv run --project engines/python agentce assess --bundle rc0 --profile rc0/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc0/out --state rc0/state --json > /dev/null || true
test ! -f rc0/out/runtime_drift.jsonl && echo "no drift on a state directory's first tracked run"
```

## A new incident lands, and the outcome flips

Append a later event the earlier run never saw — here, an `Incident` that omits the accountable actor
`INC-02` (`spec/catalogs/base/eu-ai-act/controls/INC-02.yaml`) requires — and re-assess against the same
state directory. `runtime_drift.jsonl` in the output directory lists every `(subject, control)` pair whose
outcome changed since the last tracked run, naming both outcomes and the evidence event that caused the
change; the state directory itself then remembers this pair's new outcome, for the *next* re-assessment to
compare against; and the drift file's digest lands in `manifest.json`'s own `outputs` map like every other
artifact `assess` writes, so `agentce report --validate` would catch a copy of it going missing later:

```bash
mkdir -p rc2/events rc2/state
cat > rc2/events/incidents.jsonl <<'EOF'
{"specversion":"1.0","id":"inc1","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:00:00Z","subject":"spiffe://example/agents/a","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:00:00Z","reported_at":"2026-05-02T09:00:00Z","agent":{"id":"spiffe://example/agents/a"},"integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"0000000000000000000000000000000000000000000000000000000000000000","hash":"49c190a47f041498ea688dd5d586694eddc28a5947ba9fd77daa6bf128dd69eb"}}}
EOF
DIGEST=$(shasum -a 256 rc2/events/incidents.jsonl | cut -d' ' -f1)
cat > rc2/manifest.json <<EOF
{"agentce_bundle_version": 1, "domain": "runtime-conformance-example", "sources": [{"id": "urn:example:incidents", "class": "independent_system"}], "files": [{"path": "events/incidents.jsonl", "sha256": "$DIGEST"}]}
EOF
cat > rc2/profile.yaml <<'EOF'
profile_version: 1
observation_window:
  start: "2026-05-01T00:00:00Z"
  end: "2026-08-29T00:00:00Z"
catalogs:
  - "eu-ai-act@2026.09"
subjects:
  - id: "spiffe://example/agents/a"
    role: "both"
    evidence_sources:
      - adapter: "register"
        source: "urn:example:incidents"
        class: "independent_system"
EOF
uv run --project engines/python agentce assess --bundle rc2 --profile rc2/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc2/out-1 --state rc2/state --json > /dev/null || true

cat >> rc2/events/incidents.jsonl <<'EOF'
{"specversion":"1.0","id":"inc2","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:05:00Z","subject":"spiffe://example/agents/a","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:05:00Z","reported_at":"2026-05-02T09:05:00Z","integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"49c190a47f041498ea688dd5d586694eddc28a5947ba9fd77daa6bf128dd69eb","hash":"3ec4bcaa02a13b0d58331409625469618f886cb5f95ad94798129caa502ebc58"}}}
EOF
DIGEST2=$(shasum -a 256 rc2/events/incidents.jsonl | cut -d' ' -f1)
cat > rc2/manifest.json <<EOF
{"agentce_bundle_version": 1, "domain": "runtime-conformance-example", "sources": [{"id": "urn:example:incidents", "class": "independent_system"}], "files": [{"path": "events/incidents.jsonl", "sha256": "$DIGEST2"}]}
EOF
uv run --project engines/python agentce assess --bundle rc2 --profile rc2/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc2/out-2 --state rc2/state --json > /dev/null || true # exit 2: INC-02 now reads non-conformant, and INT and REC still read insufficient evidence

cat rc2/out-2/runtime_drift.jsonl
python3 -c "import json; s = json.load(open('rc2/state/state.json')); print('remembered:', s['last_outcomes']['spiffe://example/agents/a|INC-02'])"
python3 -c "import json; m = json.load(open('rc2/out-2/manifest.json')); assert 'runtime_drift.jsonl' in m['outputs']; print('recorded in manifest.outputs:', m['outputs']['runtime_drift.jsonl'])"
```

## Drift is reported per pair, never for the whole run

Two subjects tracked in the same state directory drift independently: only the pair whose outcome
actually changed is reported, never one that stayed conformant just because something else in the run
changed:

```bash
mkdir -p rc3/events rc3/state
cat > rc3/events/incidents.jsonl <<'EOF'
{"specversion":"1.0","id":"inc-a1","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:00:00Z","subject":"spiffe://example/agents/a","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:00:00Z","reported_at":"2026-05-02T09:00:00Z","agent":{"id":"spiffe://example/agents/a"},"integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"0000000000000000000000000000000000000000000000000000000000000000","hash":"094e8492ce169969a1fc1ddf27fcd1664eb06ee336302cd665fefde0129dc2c1"}}}
{"specversion":"1.0","id":"inc-b1","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:00:00Z","subject":"spiffe://example/agents/b","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:00:00Z","reported_at":"2026-05-02T09:00:00Z","agent":{"id":"spiffe://example/agents/b"},"integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"094e8492ce169969a1fc1ddf27fcd1664eb06ee336302cd665fefde0129dc2c1","hash":"6bdf0fdc375a6d59023eef282859560fcd606fc4e2353358565a91f4b17197a5"}}}
EOF
DIGEST=$(shasum -a 256 rc3/events/incidents.jsonl | cut -d' ' -f1)
cat > rc3/manifest.json <<EOF
{"agentce_bundle_version": 1, "domain": "runtime-conformance-example", "sources": [{"id": "urn:example:incidents", "class": "independent_system"}], "files": [{"path": "events/incidents.jsonl", "sha256": "$DIGEST"}]}
EOF
cat > rc3/profile.yaml <<'EOF'
profile_version: 1
observation_window:
  start: "2026-05-01T00:00:00Z"
  end: "2026-08-29T00:00:00Z"
catalogs:
  - "eu-ai-act@2026.09"
subjects:
  - id: "spiffe://example/agents/a"
    role: "both"
    evidence_sources:
      - adapter: "register"
        source: "urn:example:incidents"
        class: "independent_system"
  - id: "spiffe://example/agents/b"
    role: "both"
    evidence_sources:
      - adapter: "register"
        source: "urn:example:incidents"
        class: "independent_system"
EOF
uv run --project engines/python agentce assess --bundle rc3 --profile rc3/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc3/out-1 --state rc3/state --json > /dev/null || true

cat >> rc3/events/incidents.jsonl <<'EOF'
{"specversion":"1.0","id":"inc-a2","source":"urn:example:incidents","type":"org.agent-conformance.evidence.Incident.v1","time":"2026-05-02T09:05:00Z","subject":"spiffe://example/agents/a","datacontenttype":"application/ld+json","agentcesourceclass":"independent_system","data":{"@context":"https://agent-conformance.org/contexts/evidence/v1","@type":"Incident","detected_at":"2026-05-02T09:05:00Z","reported_at":"2026-05-02T09:05:00Z","integrity":{"stream":"urn:example:incidents","strength":"export_chained","prev":"6bdf0fdc375a6d59023eef282859560fcd606fc4e2353358565a91f4b17197a5","hash":"7afaad868f69f6e9e9deb253d70695eee9a716dccbb0ccfc01177bcd1befe6c4"}}}
EOF
DIGEST2=$(shasum -a 256 rc3/events/incidents.jsonl | cut -d' ' -f1)
cat > rc3/manifest.json <<EOF
{"agentce_bundle_version": 1, "domain": "runtime-conformance-example", "sources": [{"id": "urn:example:incidents", "class": "independent_system"}], "files": [{"path": "events/incidents.jsonl", "sha256": "$DIGEST2"}]}
EOF
uv run --project engines/python agentce assess --bundle rc3 --profile rc3/profile.yaml --catalog "eu-ai-act@2026.09" --catalog-dir spec/catalogs/base/eu-ai-act --out rc3/out-2 --state rc3/state --json > /dev/null || true # exit 2: agent a's INC-02 now reads non-conformant, and INT and REC still read insufficient evidence
python3 -c "import json; d = [json.loads(l) for l in open('rc3/out-2/runtime_drift.jsonl')]; print([e['subject'] for e in d])"
```

## What this is not

There is no persistent watch process here: nothing polls, blocks, or runs unbounded. Every run above is
an ordinary `assess` invocation that starts, evaluates the bundle it was given, and exits. "Continuous"
means the state directory turns a series of these point-in-time runs into a trend — the cadence is
whatever supplies the next bundle: a person, a cron schedule, or a [CI job](/docs/ci-integration/) running
on every change.

## Read next

- [Running Assessments](/docs/running-assessments/) — the state directory's other role, late-event
  handling and supersession.
- [CI Integration](/docs/ci-integration/) — the cadence that makes re-running assess routine.
