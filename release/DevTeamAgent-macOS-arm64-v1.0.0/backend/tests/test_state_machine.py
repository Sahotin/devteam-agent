import pytest

from backend.app.domain.enums import TaskState
from backend.app.domain.state_machine import InvalidStateTransition, ensure_transition


def test_valid_initial_transition() -> None:
    ensure_transition(TaskState.CREATED, TaskState.REQUIREMENT_ANALYZING)


def test_rejects_skipping_workflow_stages() -> None:
    with pytest.raises(InvalidStateTransition):
        ensure_transition(TaskState.CREATED, TaskState.ARCH_APPROVAL)

