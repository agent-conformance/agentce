"""Rung-3 statistical evaluator (SPEC §7.2): metric specs computed over accepted events.

A metric selects a population from the accepted events by event type and a ``where`` filter,
optionally samples it deterministically (hash-ordered), partitions it by ``group_by``, and reduces
each group to a statistic in exact arithmetic (:mod:`agentce.numerics`). The statistic is compared
to the metric's threshold with no floating point; a ``proportion`` also carries a Clopper–Pearson
exact interval. Below ``min_population`` the outcome is ``not_assessed`` with a reason code.

Field paths in a metric (``latency_ms``, ``refs.decision.decision_type``, ``actor.id``) are
resolved against the event payload ``event["data"]``. A ``where`` value is either a plain equality
or ``subClassOf <IRI>``, the latter resolved through the domain binding's class hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from typing import Any

from . import numerics
from .domain import DomainBinding

#: Two-sided error for proportion intervals unless the catalog precision policy overrides it.
DEFAULT_ALPHA = Decimal("0.05")

CONSEQUENTIAL_MARKER = "agentce:ConsequentialDecision"


def _data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def event_type(event: dict[str, Any]) -> str:
    """The event's declared type (``data.@type``)."""
    ptype = _data(event).get("@type")
    return ptype if isinstance(ptype, str) else ""


def field_value(event: dict[str, Any], path: str) -> Any:
    """Resolve a dotted field path against the event payload; ``None`` if any segment is missing."""
    node: Any = _data(event)
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    return node


def _as_fraction(value: Any) -> Fraction:
    """Read a numeric field as an exact rational; floats never enter an outcome."""
    if isinstance(value, bool):
        raise numerics.NumericsError(f"boolean is not a numeric field value: {value!r}")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        return numerics.parse_rational(value)
    raise numerics.NumericsError(f"non-numeric field value: {value!r}")


def _ancestors(domain: DomainBinding) -> dict[str, set[str]]:
    """Each class mapped to its reflexive-transitive ancestors from the domain subclass edges."""
    result: dict[str, set[str]] = {}
    for child in domain.subclasses:
        seen: set[str] = {child}
        current: str | None = child
        while current is not None and current in domain.subclasses:
            parent = domain.subclasses[current]
            if parent in seen:
                break
            seen.add(parent)
            current = parent
        result[child] = seen
    return result


def _satisfies(
    event: dict[str, Any],
    path: str,
    matcher: str,
    domain: DomainBinding,
    ancestors: dict[str, set[str]],
) -> bool:
    value = field_value(event, path)
    if matcher.startswith("subClassOf "):
        target = matcher[len("subClassOf ") :].strip()
        if not isinstance(value, str):
            return False
        if target == CONSEQUENTIAL_MARKER and value in domain.consequential:
            return True
        return target in ancestors.get(value, {value})
    return value is not None and str(value) == matcher


def select_population(
    events: list[dict[str, Any]], population: dict[str, Any], domain: DomainBinding
) -> list[dict[str, Any]]:
    """Events of the metric's type that satisfy every ``where`` matcher (SPEC §7.2)."""
    wanted = str(population.get("event", ""))
    where = population.get("where") or {}
    ancestors = _ancestors(domain)
    selected: list[dict[str, Any]] = []
    for event in events:
        if event_type(event) != wanted:
            continue
        if all(
            _satisfies(event, path, str(matcher), domain, ancestors)
            for path, matcher in where.items()
        ):
            selected.append(event)
    return selected


def _sample_key(event: dict[str, Any]) -> tuple[str, str]:
    identity = str(event.get("id", ""))
    return (sha256(identity.encode("utf-8")).hexdigest(), identity)


def apply_sampling(
    rows: list[dict[str, Any]], sampling: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Census (all rows) or a deterministic hash-ordered sample of size ``n`` (SPEC §8.2)."""
    if not sampling or sampling.get("kind", "census") == "census":
        return rows
    if sampling.get("kind") == "hash_ordered_sample":
        n = int(sampling["n"])
        ordered = sorted(rows, key=_sample_key)
        return ordered[:n]
    raise numerics.NumericsError(f"unknown sampling kind {sampling.get('kind')!r}")


@dataclass
class StatisticValue:
    """A computed statistic: its exact value plus the presentation rounding and any interval."""

    kind: str
    value: Fraction
    presentation: Decimal
    interval: numerics.Interval | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "value": str(self.presentation)}
        if self.interval is not None:
            out["interval"] = {
                "lower": str(numerics.round_presentation(self.interval.lower)),
                "upper": str(numerics.round_presentation(self.interval.upper)),
            }
        return out


def compute_statistic(
    rows: list[dict[str, Any]],
    statistic: dict[str, Any],
    *,
    alpha: Decimal = DEFAULT_ALPHA,
) -> StatisticValue:
    """Reduce a population to its statistic in exact arithmetic (SPEC §7.2; numerics.md)."""
    kind = str(statistic["kind"])
    n = len(rows)
    if kind == "count":
        value = Fraction(n)
        return StatisticValue(kind, value, numerics.rational_to_presentation(value))
    if kind == "sum":
        total = sum(
            (_as_fraction(field_value(r, statistic["field"])) for r in rows),
            Fraction(0),
        )
        return StatisticValue(kind, total, numerics.rational_to_presentation(total))
    if kind == "mean":
        if n == 0:
            raise numerics.NumericsError("mean of an empty population")
        total = sum(
            (_as_fraction(field_value(r, statistic["field"])) for r in rows),
            Fraction(0),
        )
        value = total / n
        return StatisticValue(kind, value, numerics.rational_to_presentation(value))
    if kind == "quantile":
        values = [_as_fraction(field_value(r, statistic["field"])) for r in rows]
        q = numerics.parse_rational(str(statistic["q"]))
        value = numerics.nearest_rank_quantile(values, q)
        return StatisticValue(kind, value, numerics.rational_to_presentation(value))
    if kind == "ratio":
        num = sum(1 for r in rows if field_value(r, statistic["numerator"]) is True)
        den = sum(1 for r in rows if field_value(r, statistic["denominator"]) is True)
        if den == 0:
            raise numerics.NumericsError("ratio with an empty denominator")
        value = Fraction(num, den)
        return StatisticValue(kind, value, numerics.rational_to_presentation(value))
    if kind == "proportion":
        successes = sum(1 for r in rows if field_value(r, statistic["field"]) is True)
        if n == 0:
            raise numerics.NumericsError("proportion of an empty population")
        value = Fraction(successes, n)
        interval = numerics.clopper_pearson(successes, n, alpha)
        return StatisticValue(
            kind, value, numerics.rational_to_presentation(value), interval
        )
    raise numerics.NumericsError(f"unknown statistic kind {kind!r}")


@dataclass
class GroupResult:
    """One group's outcome: its key, population size, statistic, and decided outcome."""

    group: dict[str, str]
    population: int
    outcome: str
    reason: str | None = None
    statistic: StatisticValue | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "group": self.group,
            "population": self.population,
            "outcome": self.outcome,
        }
        if self.reason is not None:
            out["reason"] = self.reason
        if self.statistic is not None:
            out["statistic"] = self.statistic.to_json()
        return out


@dataclass
class MetricResult:
    """A metric's overall outcome and its per-group detail (SPEC §7.2)."""

    metric: str
    outcome: str
    groups: list[GroupResult] = dataclass_field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "outcome": self.outcome,
            "groups": [g.to_json() for g in self.groups],
        }


def _group_key(
    event: dict[str, Any], group_by: list[str]
) -> tuple[tuple[str, str], ...]:
    return tuple((path, str(field_value(event, path))) for path in group_by)


def _aggregate(groups: list[GroupResult], fail_outcome: str, pass_outcome: str) -> str:
    if any(g.outcome == fail_outcome for g in groups):
        return fail_outcome
    if groups and all(g.outcome == "not_assessed" for g in groups):
        return "not_assessed"
    if not groups:
        return "not_assessed"
    return pass_outcome


def evaluate_metric(
    events: list[dict[str, Any]],
    metric: dict[str, Any],
    domain: DomainBinding,
    *,
    alpha: Decimal = DEFAULT_ALPHA,
) -> MetricResult:
    """Evaluate one metric spec over the accepted events to a :class:`MetricResult` (SPEC §7.2)."""
    metric_id = str(metric["id"])
    min_population = int(metric["min_population"])
    outcome_map = metric["outcome_map"]
    pass_outcome = str(outcome_map["pass"])
    fail_outcome = str(outcome_map["fail"])
    threshold = metric["threshold"]
    op = str(threshold["op"])
    rhs = _as_fraction(threshold["value"])
    group_by = list(metric.get("group_by") or [])

    population = select_population(events, metric["population"], domain)
    population = apply_sampling(population, metric.get("sampling"))

    partitions: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = {}
    if group_by:
        for event in population:
            partitions.setdefault(_group_key(event, group_by), []).append(event)
    else:
        partitions[()] = population

    groups: list[GroupResult] = []
    for key, rows in partitions.items():
        group = {path: value for path, value in key}
        if len(rows) < min_population:
            groups.append(
                GroupResult(
                    group, len(rows), "not_assessed", reason="below_min_population"
                )
            )
            continue
        stat = compute_statistic(rows, metric["statistic"], alpha=alpha)
        passed = numerics.compare(stat.value, op, rhs)
        groups.append(
            GroupResult(
                group,
                len(rows),
                pass_outcome if passed else fail_outcome,
                statistic=stat,
            )
        )

    groups.sort(key=lambda g: sorted(g.group.items()))
    return MetricResult(
        metric_id, _aggregate(groups, fail_outcome, pass_outcome), groups
    )
