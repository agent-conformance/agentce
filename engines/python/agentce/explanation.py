"""Explanation renderer (SPEC §8.1, §9.3): narratives for consequential decisions with edge citations.

For each decision the renderer fills five mandatory slots — the role of the AI, the inputs relied
upon, the options weighed, the option chosen, and the human involvement — from the graph and the
recorded decision payload. Every sentence carries one or more ``[edge:<iri>]`` citations to the fact
that supports it. A mandatory slot the record cannot fill renders "not reconstructable from record"
(SPEC §8.1 pipeline contract): the narrative never invents a fact it cannot cite.

Nothing here is generative: the templates are fixed and the citations are graph IRIs, so the same
graph yields byte-identical narratives on any engine (the golden narratives in the conformance suite
pin them, P3.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .domain import DomainBinding
from .iri import event_iri
from .store import GraphStore

NOT_RECONSTRUCTABLE = "not reconstructable from record"
MANDATORY_SLOTS = ("role", "inputs", "options", "chosen", "human")


def _data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


@dataclass
class Sentence:
    """One narrative sentence and the edge IRIs it cites."""

    slot: str
    text: str
    citations: list[str]
    reconstructable: bool = True

    def render(self) -> str:
        cites = " ".join(f"[edge:{iri}]" for iri in self.citations)
        return f"{self.text} {cites}".rstrip()


@dataclass
class Narrative:
    """A decision's explanation: its sentences and whether every mandatory slot was filled."""

    decision: str
    sentences: list[Sentence]

    @property
    def reconstructable(self) -> bool:
        return all(s.reconstructable for s in self.sentences)

    def render(self) -> str:
        head = f"Decision {self.decision}"
        if not self.reconstructable:
            head += f" ({NOT_RECONSTRUCTABLE})"
        body = "\n".join(f"- {s.render()}" for s in self.sentences)
        return f"{head}\n{body}\n"


def _role(event: dict[str, Any], node: str, store: GraphStore) -> Sentence:
    data = _data(event)
    agents = sorted(store.objects(node, "prov:wasAssociatedWith"))
    anchor = agents[0] if agents else node
    ai_role = data.get("ai_role")
    if isinstance(ai_role, str) and ai_role:
        return Sentence("role", f"The AI acted in the role '{ai_role}'.", [anchor])
    return Sentence(
        "role", f"The role of the AI is {NOT_RECONSTRUCTABLE}.", [node], False
    )


def _inputs(node: str, store: GraphStore) -> Sentence:
    used = sorted(store.objects(node, "prov:used"))
    if used:
        listed = ", ".join(used)
        return Sentence("inputs", f"It relied on recorded inputs: {listed}.", used)
    return Sentence(
        "inputs", f"The inputs relied upon are {NOT_RECONSTRUCTABLE}.", [node], False
    )


def _options(event: dict[str, Any], node: str) -> Sentence:
    options = _data(event).get("options")
    if isinstance(options, list) and options:
        iris: list[str] = []
        labels: list[str] = []
        for opt in options:
            if isinstance(opt, dict) and isinstance(opt.get("id"), str):
                iris.append(f"{node}/option/{opt['id']}")
                labels.append(str(opt.get("label", opt["id"])))
        if iris:
            return Sentence(
                "options", f"It weighed the options: {', '.join(labels)}.", iris
            )
    return Sentence(
        "options", f"The options considered are {NOT_RECONSTRUCTABLE}.", [node], False
    )


def _chosen(event: dict[str, Any], node: str) -> Sentence:
    data = _data(event)
    chosen = data.get("chosen")
    raw_options = data.get("options")
    options = raw_options if isinstance(raw_options, list) else []
    labels = {
        opt["id"]: str(opt.get("label", opt["id"]))
        for opt in options
        if isinstance(opt, dict) and isinstance(opt.get("id"), str)
    }
    if isinstance(chosen, str) and chosen in labels:
        return Sentence(
            "chosen", f"It chose '{labels[chosen]}'.", [f"{node}/option/{chosen}"]
        )
    return Sentence(
        "chosen", f"The option chosen is {NOT_RECONSTRUCTABLE}.", [node], False
    )


def _human(node: str, store: GraphStore) -> Sentence:
    for principal in sorted(store.objects(node, "agentce:chainTerminus")):
        if store.is_a(principal, "agentce:HumanPrincipal"):
            return Sentence(
                "human",
                "A human principal was accountable for this decision.",
                [principal],
            )
    reviews = sorted(store.objects(node, "agentce:reviewedBy"))
    if reviews:
        return Sentence("human", "A human reviewed this decision.", reviews)
    return Sentence(
        "human", f"Human involvement is {NOT_RECONSTRUCTABLE}.", [node], False
    )


def render_decision(event: dict[str, Any], store: GraphStore) -> Narrative:
    """Render the explanation for one decision event over the graph (SPEC §8.1)."""
    node = event_iri(str(event["id"]))
    sentences = [
        _role(event, node, store),
        _inputs(node, store),
        _options(event, node),
        _chosen(event, node),
        _human(node, store),
    ]
    return Narrative(node, sentences)


def consequential_decisions(
    events: list[dict[str, Any]], domain: DomainBinding
) -> list[dict[str, Any]]:
    """The decision events whose declared type the domain marks consequential (SPEC §7.4)."""
    out: list[dict[str, Any]] = []
    for event in events:
        data = _data(event)
        if (
            data.get("@type") == "Decision"
            and str(data.get("decision_type", "")) in domain.consequential
        ):
            out.append(event)
    return out


def sample_by_iri_hash(events: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """The first ``k`` decisions ordered by the hash of their node IRI (SPEC §9.3 item 8)."""
    ordered = sorted(
        events,
        key=lambda e: sha256(event_iri(str(e["id"])).encode("utf-8")).hexdigest(),
    )
    return ordered[:k]


def render_sample(
    events: list[dict[str, Any]],
    store: GraphStore,
    domain: DomainBinding,
    *,
    k: int | None = None,
) -> list[Narrative]:
    """Render narratives for a deterministic sample of consequential decisions (SPEC §9.3)."""
    decisions = consequential_decisions(events, domain)
    chosen = (
        sample_by_iri_hash(decisions, k)
        if k is not None
        else sorted(
            decisions,
            key=lambda e: sha256(event_iri(str(e["id"])).encode("utf-8")).hexdigest(),
        )
    )
    return [render_decision(event, store) for event in chosen]
