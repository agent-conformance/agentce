"""Fresh-machine proof (SPEC §13.4 AX-1, §14.4, P5.7): the quickstart workflow, the per-engine pages,
and the engine image are all present and correctly configured, and no image is pushed.
"""

from __future__ import annotations

import fresh_machine


def test_all_conditions_hold() -> None:
    result = fresh_machine.run()
    assert result == {"quickstart_ci": True, "pages": True, "image_builds": True}


def test_quickstart_workflow_covers_every_engine() -> None:
    assert fresh_machine._quickstart_ci() is True


def test_per_engine_pages_exist() -> None:
    assert fresh_machine._pages() is True


def test_the_image_builds_without_a_push() -> None:
    assert fresh_machine._image_builds() is True
    assert "docker push" not in fresh_machine.WORKFLOW.read_text("utf-8")
