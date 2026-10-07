#!/usr/bin/env bash
# Build gate helper for VG-RECORDS-PROFILE-ROUND-TRIP (item 18.77): the profile a records-folder run
# derives says "Edit it ... and pass it back with --profile", so passing it back has to work. Over a
# folder with two agents and id-less records (the otel-genai adapter's datadog, langfuse and
# openinference-rag fixtures):
#   R1  the derived profile passed back unedited gives the same assertions.json, byte for byte, and
#       the same project.json rows (each agent's own records, verdict and counts) with every agent declared;
#   R2  with one agent removed from the profile, that agent keeps its own records and row, is listed as
#       undeclared, and its tools and models stay undeclared;
#   R3  a profile naming two agents over a folder with one named agent and id-less records puts the
#       id-less records on agentce:subject/local, never on the named agent;
#   R4  the same profile with its subjects reversed gives the same assertions (as a set) and the same
#       project.json.
#
# Python only for now: TypeScript and Java have no records-folder mode until item 18.69 ports it.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fx="$root/adapters/otel-genai/fixtures"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/rec" "$work/one"
cp "$fx/datadog/input.json" "$work/rec/fraud.json"
cp "$fx/langfuse/input.json" "$work/rec/checkout.json"
cp "$fx/openinference-rag/input.json" "$work/rec/rag.json"
cp "$fx/otel-genai-agent-session/input.json" "$work/one/session.json"
cp "$fx/openinference-rag/input.json" "$work/one/rag.json"

cd "$root/engines/python"
env -u VIRTUAL_ENV uv run --frozen python - "$work" <<'PY'
import contextlib
import io
import json
import sys
from pathlib import Path

import yaml

from agentce import cli

work = Path(sys.argv[1])
FRAUD = "spiffe://corp/agents/fraud-detection-agent"
LOCAL = "agentce:subject/local"
problems: list[str] = []


def assess(name: str, folder: str, profile: dict | None = None) -> Path:
    out = work / name
    argv = ["assess", str(work / folder), "--out", str(out), "--json"]
    if profile is not None:
        path = work / f"{name}.yaml"
        path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        argv[2:2] = ["--profile", str(path)]
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        code = cli.main(argv)
    if code != 0:
        raise SystemExit(f"records-profile-round-trip: {name} exited {code}: {buf.getvalue()[-300:]}")
    return out


def load(out: Path, name: str):
    return json.loads((out / name).read_text(encoding="utf-8"))


def check(rid: str, ok: bool, detail: str) -> None:
    print(f"records-profile-round-trip: {rid} {'ok' if ok else 'FAIL'}: {detail}")
    if not ok:
        problems.append(rid)


first = assess("first", "rec")
derived = yaml.safe_load((first / "applicability.yaml").read_text(encoding="utf-8"))
first_assertions = sorted(map(json.dumps, load(first, "assertions.json")))

second = assess("second", "rec", derived)
project = load(second, "project.json")
check(
    "R1",
    (second / "assertions.json").read_bytes() == (first / "assertions.json").read_bytes()
    and len(project["agents"]) == 3
    and project["agents"] == [{**a, "declared": True} for a in load(first, "project.json")["agents"]]
    and all(a["declared"] for a in project["agents"])
    and project["undeclared_agents"] == [],
    f"declared {[a['declared'] for a in project['agents']]}, undeclared {project['undeclared_agents']}",
)

drop = assess("drop", "rec", {**derived, "subjects": [s for s in derived["subjects"] if s["id"] != FRAUD]})
rows = {a["id"]: a for a in load(drop, "project.json")["agents"]}
undeclared = load(drop, "activity.json")["undeclared"]
check(
    "R2",
    FRAUD in rows
    and rows[FRAUD]["declared"] is False
    and rows[FRAUD]["agents_observed"] == [FRAUD]
    and load(drop, "project.json")["undeclared_agents"] == [FRAUD]
    and undeclared == {"agents": [FRAUD], "models": ["gemini-1.5-pro"], "tools": ["sanctions_screen"]}
    and sorted(map(json.dumps, load(drop, "assertions.json"))) == first_assertions,
    f"undeclared {undeclared}",
)

one = assess("one-first", "one")
(named,) = yaml.safe_load((one / "applicability.yaml").read_text(encoding="utf-8"))["subjects"]
two = assess("two", "one", {**derived, "subjects": [named, {**named, "id": FRAUD}]})
agents_by_subject: dict[str, set] = {}
for f in sorted((two / "records-bundle" / "events").iterdir()):
    for line in f.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        agent = event["data"].get("agent")
        agents_by_subject.setdefault(event["subject"], set()).add(
            agent.get("id") if isinstance(agent, dict) else None
        )
check(
    "R3",
    agents_by_subject.get(named["id"]) == {named["id"]} and agents_by_subject.get(LOCAL) == {None},
    f"{ {k: sorted(map(str, v)) for k, v in agents_by_subject.items()} }",
)

rev = assess("rev", "rec", {**derived, "subjects": list(reversed(derived["subjects"]))})
check(
    "R4",
    sorted(map(json.dumps, load(rev, "assertions.json"))) == first_assertions
    and load(rev, "project.json") == project,
    "reversed subjects",
)

if problems:
    raise SystemExit(f"records-profile-round-trip: FAIL {', '.join(problems)}")
print("records-profile-round-trip: R1-R4 ok")
PY
