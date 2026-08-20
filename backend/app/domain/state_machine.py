from __future__ import annotations

from backend.app.domain.enums import TaskState


class InvalidStateTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {
        TaskState.REQUIREMENT_ANALYZING,
        TaskState.PAUSED,
        TaskState.CANCELLED,
    },
    TaskState.REQUIREMENT_ANALYZING: {
        TaskState.PRD_APPROVAL,
        TaskState.FAILED,
        TaskState.PAUSED,
    },
    TaskState.PRD_APPROVAL: {
        TaskState.ARCHITECTING,
        TaskState.REQUIREMENT_ANALYZING,
        TaskState.PAUSED,
        TaskState.CANCELLED,
    },
    TaskState.ARCHITECTING: {
        TaskState.ARCH_APPROVAL,
        TaskState.FAILED,
        TaskState.PAUSED,
    },
    TaskState.ARCH_APPROVAL: {
        TaskState.CODING,
        TaskState.COMPLETED,
        TaskState.ARCHITECTING,
        TaskState.PAUSED,
        TaskState.CANCELLED,
    },
    TaskState.CODING: {
        TaskState.REVIEWING,
        TaskState.FAILED,
        TaskState.PAUSED,
    },
    TaskState.REVIEWING: {
        TaskState.CODING,
        TaskState.TESTING,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.PAUSED,
        TaskState.CANCELLED,
    },
    TaskState.TESTING: {
        TaskState.CODING,
        TaskState.DEPENDENCY_APPROVAL,
        TaskState.FINAL_VALIDATION,
        TaskState.FAILED,
        TaskState.PAUSED,
        TaskState.CANCELLED,
    },
    TaskState.DEPENDENCY_APPROVAL: {
        TaskState.TESTING,
        TaskState.PAUSED,
        TaskState.CANCELLED,
    },
    TaskState.FINAL_VALIDATION: {
        TaskState.CODING,
        TaskState.COMPLETED,
        TaskState.FAILED,
    },
    TaskState.RETRYING: {TaskState.FAILED},
    TaskState.FAILED: {
        TaskState.CREATED,
        TaskState.PRD_APPROVAL,
        TaskState.ARCH_APPROVAL,
        TaskState.REVIEWING,
        TaskState.TESTING,
    },
    TaskState.PAUSED: {
        TaskState.CREATED,
        TaskState.REQUIREMENT_ANALYZING,
        TaskState.PRD_APPROVAL,
        TaskState.ARCHITECTING,
        TaskState.ARCH_APPROVAL,
        TaskState.CODING,
        TaskState.REVIEWING,
        TaskState.TESTING,
        TaskState.DEPENDENCY_APPROVAL,
        TaskState.CANCELLED,
    },
}


PAUSABLE_STATES = frozenset(
    {
        TaskState.CREATED,
        TaskState.PRD_APPROVAL,
        TaskState.ARCH_APPROVAL,
        TaskState.REVIEWING,
        TaskState.TESTING,
        TaskState.DEPENDENCY_APPROVAL,
    }
)

TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)


def ensure_transition(current: TaskState, target: TaskState) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransition(f"cannot transition task from {current} to {target}")
