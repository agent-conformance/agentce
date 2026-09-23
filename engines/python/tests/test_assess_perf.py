"""assess_subjects scans the accepted-event list a number of times independent of subject count."""

from __future__ import annotations

from typing import Any

from agentce.assess import assess_subjects
from agentce.domain import DomainBinding
from agentce.profile import Profile

_EVENT_COUNT = 300
_MANY_SUBJECTS = 60


class _CountingList(list[Any]):
    """A list that counts every element touched via iteration or indexing."""

    def __init__(self, *args: Any, counter: dict[str, int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._counter = counter

    def __iter__(self) -> Any:
        for item in list.__iter__(self):
            self._counter["n"] += 1
            yield item

    def __getitem__(self, key: Any) -> Any:
        self._counter["n"] += 1
        return list.__getitem__(self, key)


def _events() -> list[dict[str, Any]]:
    return [
        {
            "id": f"e{i}",
            "subject": "ghost",
            "time": "2024-01-01T00:00:00Z",
            "agentcesourceclass": "operator",
            "data": {"@type": "ToolCall"},
        }
        for i in range(_EVENT_COUNT)
    ]


def _scans(num_subjects: int) -> int:
    counter = {"n": 0}
    profile = Profile.from_dict(
        {"subjects": [{"id": f"s{j}"} for j in range(num_subjects)]}
    )
    accepted = _CountingList(_events(), counter=counter)
    assess_subjects(accepted, profile, [], DomainBinding.empty())
    return counter["n"]


def test_accepted_event_scans_do_not_grow_with_subject_count() -> None:
    """The accepted list is indexed by subject once; scanning it must not scale with subjects.

    A per-subject rescan of the accepted list (O(subjects x events)) makes the many-subject
    scan count a multiple of the one-subject count; an index built once keeps them equal.
    """
    one = _scans(1)
    many = _scans(_MANY_SUBJECTS)
    assert one > 0
    assert many == one
