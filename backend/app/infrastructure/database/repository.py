from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import timezone
from typing import Iterator
from uuid import uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.domain.enums import (
    ArtifactType,
    ExecutionAction,
    ExecutionStatus,
    ExecutionScope,
    IterationKind,
    MemoryStatus,
    MemoryType,
    DeliveryPreference,
    TaskState,
    ToolCallStatus,
)
from backend.app.domain.memory import MemoryCreate, MemoryRecord
from backend.app.domain.evaluation import (
    TaskEvaluationFeedbackCreate,
    TaskEvaluationFeedbackRecord,
)
from backend.app.domain.models import (
    ArtifactRecord,
    CheckpointRecord,
    EventRecord,
    ExecutionRecord,
    ProjectCreate,
    ProjectRecord,
    ProjectWorkspaceUpdate,
    TaskCreate,
    TaskIterationCreate,
    TaskRecord,
    ToolCallRecord,
)
from backend.app.domain.state_machine import ensure_transition
from backend.app.domain.rag import CodeChunk, IndexedFileRecord, IndexStats
from backend.app.domain.task_title import build_task_title
from backend.app.domain.task_policy import TaskComplexityPolicy, TaskPolicyDecision
from backend.app.infrastructure.database.tables import (
    ArtifactRow,
    CodeChunkRow,
    CheckpointRow,
    EventRow,
    ExecutionRow,
    IndexedFileRow,
    MemoryRow,
    ProjectRow,
    TaskRow,
    TaskPolicyRow,
    TaskEvaluationFeedbackRow,
    ToolCallRow,
    utc_now,
)


class EntityNotFoundError(LookupError):
    pass


class ConcurrentStateChangeError(RuntimeError):
    pass


class ActiveExecutionExistsError(ValueError):
    pass


class SqlAlchemyRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_project(self, command: ProjectCreate) -> ProjectRecord:
        with self._session() as session:
            row = ProjectRow(id=str(uuid4()), **command.model_dump())
            session.add(row)
            session.flush()
            return ProjectRecord.model_validate(row)

    def ping(self) -> bool:
        try:
            with self._session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def get_project(self, project_id: str) -> ProjectRecord:
        with self._session() as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise EntityNotFoundError(f"project {project_id} was not found")
            return ProjectRecord.model_validate(row)

    def list_projects(self) -> list[ProjectRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(ProjectRow).order_by(ProjectRow.created_at.desc())
            ).all()
            return [ProjectRecord.model_validate(row) for row in rows]

    def update_project_workspace(
        self, project_id: str, command: ProjectWorkspaceUpdate
    ) -> ProjectRecord:
        with self._session() as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise EntityNotFoundError(f"project {project_id} was not found")
            row.root_path = command.root_path
            session.flush()
            return ProjectRecord.model_validate(row)

    def create_task(
        self,
        command: TaskCreate,
        policy: TaskPolicyDecision | None = None,
    ) -> TaskRecord:
        with self._session() as session:
            project = session.get(ProjectRow, command.project_id)
            if project is None:
                raise EntityNotFoundError(f"project {command.project_id} was not found")
            decision = policy or TaskComplexityPolicy().assess(
                requirement=command.requirement,
                execution_scope=command.execution_scope,
                preference=command.preference,
                workspace_root=project.root_path,
            )
            row = TaskRow(
                id=str(uuid4()),
                project_id=command.project_id,
                requirement=command.requirement,
                state=TaskState.CREATED.value,
                state_version=1,
            )
            session.add(row)
            session.flush()
            row.policy = self._task_policy_row(row.id, decision)
            session.add(
                EventRow(
                    task_id=row.id,
                    event_type="task.created",
                    payload={
                        "state": row.state,
                        "governance_level": decision.governance_level.value,
                        "risk_score": decision.risk_score,
                    },
                )
            )
            return self._task_record(row)

    def create_iteration_task(
        self,
        source_task_id: str,
        command: TaskIterationCreate,
        policy: TaskPolicyDecision | None = None,
    ) -> TaskRecord:
        with self._session() as session:
            source = session.get(TaskRow, source_task_id)
            if source is None:
                raise EntityNotFoundError(f"task {source_task_id} was not found")
            source_state = TaskState(source.state)
            if source_state is TaskState.FAILED:
                if command.kind is not IterationKind.BUG_FIX:
                    raise ValueError("失败任务只能创建故障修复迭代")
            elif source_state is not TaskState.COMPLETED:
                raise ValueError("只有已完成或执行失败的任务可以创建迭代")
            project = session.get(ProjectRow, source.project_id)
            assert project is not None
            decision = policy or TaskComplexityPolicy().assess(
                requirement=command.request,
                execution_scope=command.execution_scope,
                preference=command.preference,
                workspace_root=project.root_path,
            )
            row = TaskRow(
                id=str(uuid4()),
                project_id=source.project_id,
                requirement=command.request,
                state=TaskState.CREATED.value,
                state_version=1,
            )
            session.add(row)
            session.flush()
            row.policy = self._task_policy_row(row.id, decision)
            session.add_all(
                [
                    EventRow(
                        task_id=row.id,
                        event_type="task.created",
                        payload={
                            "state": row.state,
                            "source": "iteration",
                            "governance_level": decision.governance_level.value,
                            "risk_score": decision.risk_score,
                        },
                    ),
                    EventRow(
                        task_id=row.id,
                        event_type="task.iteration_created",
                        payload={
                            "parent_task_id": source.id,
                            "parent_requirement": source.requirement,
                            "iteration_kind": command.kind.value,
                        },
                    ),
                ]
            )
            return self._task_record(row)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._session() as session:
            row = session.get(TaskRow, task_id)
            if row is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            return self._task_record(row)

    def list_tasks(self, project_id: str | None = None) -> list[TaskRecord]:
        with self._session() as session:
            query = select(TaskRow)
            if project_id:
                if session.get(ProjectRow, project_id) is None:
                    raise EntityNotFoundError(
                        f"project {project_id} was not found"
                    )
                query = query.where(TaskRow.project_id == project_id)
            rows = session.scalars(query.order_by(TaskRow.created_at.desc())).all()
            return [self._task_record(row) for row in rows]

    def get_evaluation_feedback(
        self, task_id: str
    ) -> TaskEvaluationFeedbackRecord | None:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            row = session.get(TaskEvaluationFeedbackRow, task_id)
            return self._evaluation_feedback_record(row) if row else None

    def save_evaluation_feedback(
        self,
        task_id: str,
        command: TaskEvaluationFeedbackCreate,
    ) -> TaskEvaluationFeedbackRecord:
        with self._session() as session:
            task = session.get(TaskRow, task_id)
            if task is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            if TaskState(task.state) is not TaskState.COMPLETED:
                raise ValueError("任务完成后才能提交成果评价")
            now = utc_now()
            row = session.get(TaskEvaluationFeedbackRow, task_id)
            if row is None:
                row = TaskEvaluationFeedbackRow(
                    task_id=task_id,
                    created_at=now,
                    updated_at=now,
                    **command.model_dump(),
                )
                session.add(row)
            else:
                row.rating = command.rating
                row.accepted = command.accepted
                row.comment = command.comment
                row.updated_at = now
            session.add(
                EventRow(
                    task_id=task_id,
                    event_type="evaluation.feedback_recorded",
                    payload={
                        "rating": command.rating,
                        "accepted": command.accepted,
                        "comment_present": bool(command.comment.strip()),
                    },
                )
            )
            session.flush()
            return self._evaluation_feedback_record(row)

    def transition_task(
        self,
        task_id: str,
        target: TaskState,
        *,
        expected_version: int,
        event_payload: dict | None = None,
        error_message: str | None = None,
    ) -> TaskRecord:
        with self._session() as session:
            row = session.get(TaskRow, task_id)
            if row is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            current = TaskState(row.state)
            ensure_transition(current, target)
            next_version = expected_version + 1
            result = session.execute(
                update(TaskRow)
                .where(TaskRow.id == task_id, TaskRow.state_version == expected_version)
                .values(
                    state=target.value,
                    state_version=next_version,
                    error_message=error_message,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                raise ConcurrentStateChangeError(
                    f"task {task_id} changed while transitioning from version {expected_version}"
                )
            payload = {"from": current.value, "to": target.value}
            payload.update(event_payload or {})
            session.add(
                EventRow(task_id=task_id, event_type="task.state_changed", payload=payload)
            )
            session.flush()
            updated = session.get(TaskRow, task_id)
            assert updated is not None
            return self._task_record(updated)

    def save_artifact(
        self,
        task_id: str,
        artifact_type: ArtifactType,
        created_by: str,
        content: dict,
    ) -> ArtifactRecord:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            latest_version = session.scalar(
                select(func.max(ArtifactRow.version)).where(
                    ArtifactRow.task_id == task_id,
                    ArtifactRow.type == artifact_type.value,
                )
            )
            row = ArtifactRow(
                id=str(uuid4()),
                task_id=task_id,
                type=artifact_type.value,
                version=(latest_version or 0) + 1,
                created_by=created_by,
                content=content,
            )
            session.add(row)
            session.flush()
            session.add(
                EventRow(
                    task_id=task_id,
                    event_type="artifact.created",
                    payload={
                        "artifact_id": row.id,
                        "type": row.type,
                        "version": row.version,
                    },
                )
            )
            return self._artifact_record(row)

    def latest_artifact(
        self, task_id: str, artifact_type: ArtifactType
    ) -> ArtifactRecord:
        with self._session() as session:
            row = session.scalar(
                select(ArtifactRow)
                .where(
                    ArtifactRow.task_id == task_id,
                    ArtifactRow.type == artifact_type.value,
                )
                .order_by(ArtifactRow.version.desc())
                .limit(1)
            )
            if row is None:
                raise EntityNotFoundError(
                    f"task {task_id} has no {artifact_type.value} artifact"
                )
            return self._artifact_record(row)

    def list_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(ArtifactRow)
                .where(ArtifactRow.task_id == task_id)
                .order_by(ArtifactRow.created_at, ArtifactRow.version)
            ).all()
            return [self._artifact_record(row) for row in rows]

    def save_checkpoint(self, task: TaskRecord, snapshot: dict) -> CheckpointRecord:
        with self._session() as session:
            row = CheckpointRow(
                task_id=task.id,
                state=task.state.value,
                state_version=task.state_version,
                snapshot=snapshot,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return CheckpointRecord(
                id=row.id,
                task_id=row.task_id,
                state=TaskState(row.state),
                state_version=row.state_version,
                snapshot=row.snapshot,
                created_at=row.created_at,
            )

    def latest_checkpoint(self, task_id: str) -> CheckpointRecord:
        with self._session() as session:
            row = session.scalar(
                select(CheckpointRow)
                .where(CheckpointRow.task_id == task_id)
                .order_by(CheckpointRow.id.desc())
                .limit(1)
            )
            if row is None:
                raise EntityNotFoundError(f"task {task_id} has no checkpoint")
            return CheckpointRecord(
                id=row.id,
                task_id=row.task_id,
                state=TaskState(row.state),
                state_version=row.state_version,
                snapshot=row.snapshot,
                created_at=row.created_at,
            )

    def list_events(self, task_id: str) -> list[EventRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(EventRow)
                .where(EventRow.task_id == task_id)
                .order_by(EventRow.id)
            ).all()
            return [
                EventRecord(
                    id=row.id,
                    task_id=row.task_id,
                    event_type=row.event_type,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def list_recent_events(
        self,
        task_id: str,
        *,
        limit: int = 200,
        before_id: int | None = None,
    ) -> list[EventRecord]:
        """按时间正序返回最近一页事件，避免长任务一次加载全部审计记录。"""
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            statement = select(EventRow).where(EventRow.task_id == task_id)
            if before_id is not None:
                statement = statement.where(EventRow.id < before_id)
            rows = list(
                session.scalars(
                    statement.order_by(EventRow.id.desc()).limit(limit)
                ).all()
            )
            rows.reverse()
            return [
                EventRecord(
                    id=row.id,
                    task_id=row.task_id,
                    event_type=row.event_type,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def record_event(self, task_id: str, event_type: str, payload: dict) -> None:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            session.add(
                EventRow(
                    task_id=task_id,
                    event_type=event_type,
                    payload=payload,
                )
            )

    def list_events_after(
        self, task_id: str, after_id: int = 0, limit: int = 200
    ) -> list[EventRecord]:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            rows = session.scalars(
                select(EventRow)
                .where(EventRow.task_id == task_id, EventRow.id > after_id)
                .order_by(EventRow.id)
                .limit(limit)
            ).all()
            return [
                EventRecord(
                    id=row.id,
                    task_id=row.task_id,
                    event_type=row.event_type,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def event_count(self, task_id: str) -> int:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            return session.scalar(
                select(func.count()).select_from(EventRow).where(
                    EventRow.task_id == task_id
                )
            ) or 0

    def create_execution(
        self,
        task_id: str,
        action: ExecutionAction,
        payload: dict,
    ) -> ExecutionRecord:
        try:
            with self._session() as session:
                if session.get(TaskRow, task_id) is None:
                    raise EntityNotFoundError(f"task {task_id} was not found")
                row = ExecutionRow(
                    id=str(uuid4()),
                    task_id=task_id,
                    active_task_id=task_id,
                    action=action.value,
                    payload=payload,
                    status=ExecutionStatus.QUEUED.value,
                    attempt=0,
                )
                session.add(row)
                session.flush()
                session.add(
                    EventRow(
                        task_id=task_id,
                        event_type="execution.queued",
                        payload={
                            "execution_id": row.id,
                            "action": action.value,
                        },
                    )
                )
                return self._execution_record(row)
        except IntegrityError as error:
            raise ActiveExecutionExistsError(
                f"task {task_id} already has an active execution"
            ) from error

    def get_execution(self, execution_id: str) -> ExecutionRecord:
        with self._session() as session:
            row = session.get(ExecutionRow, execution_id)
            if row is None:
                raise EntityNotFoundError(
                    f"execution {execution_id} was not found"
                )
            return self._execution_record(row)

    def record_execution_progress(
        self,
        execution_id: str,
        percent: int,
        step: str,
        detail: str,
    ) -> None:
        with self._session() as session:
            row = session.get(ExecutionRow, execution_id)
            if row is None:
                raise EntityNotFoundError(
                    f"execution {execution_id} was not found"
                )
            if row.status != ExecutionStatus.RUNNING.value:
                return
            session.add(
                EventRow(
                    task_id=row.task_id,
                    event_type="execution.progress",
                    payload={
                        "execution_id": row.id,
                        "action": row.action,
                        "percent": max(0, min(100, percent)),
                        "step": step[:120],
                        "detail": detail[:500],
                    },
                )
            )

    def list_executions(self, task_id: str) -> list[ExecutionRecord]:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            rows = session.scalars(
                select(ExecutionRow)
                .where(ExecutionRow.task_id == task_id)
                .order_by(ExecutionRow.created_at, ExecutionRow.id)
            ).all()
            return [self._execution_record(row) for row in rows]

    def queued_executions(self) -> list[ExecutionRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(ExecutionRow)
                .where(ExecutionRow.status == ExecutionStatus.QUEUED.value)
                .order_by(ExecutionRow.created_at, ExecutionRow.id)
            ).all()
            return [self._execution_record(row) for row in rows]

    def claim_execution(self, execution_id: str) -> ExecutionRecord | None:
        with self._session() as session:
            result = session.execute(
                update(ExecutionRow)
                .where(
                    ExecutionRow.id == execution_id,
                    ExecutionRow.status == ExecutionStatus.QUEUED.value,
                )
                .values(
                    status=ExecutionStatus.RUNNING.value,
                    attempt=ExecutionRow.attempt + 1,
                    started_at=utc_now(),
                    finished_at=None,
                    error_message=None,
                )
            )
            if result.rowcount != 1:
                return None
            row = session.get(ExecutionRow, execution_id)
            assert row is not None
            session.add(
                EventRow(
                    task_id=row.task_id,
                    event_type="execution.started",
                    payload={
                        "execution_id": row.id,
                        "action": row.action,
                        "attempt": row.attempt,
                    },
                )
            )
            session.flush()
            return self._execution_record(row)

    def finish_execution(
        self,
        execution_id: str,
        status: ExecutionStatus,
        *,
        result_state: TaskState | None = None,
        error_message: str | None = None,
    ) -> ExecutionRecord:
        if status not in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }:
            raise ValueError("execution can only finish in a terminal status")
        with self._session() as session:
            row = session.get(ExecutionRow, execution_id)
            if row is None:
                raise EntityNotFoundError(
                    f"execution {execution_id} was not found"
                )
            row.status = status.value
            row.active_task_id = None
            row.result_state = result_state.value if result_state else None
            row.error_message = error_message
            row.finished_at = utc_now()
            session.add(
                EventRow(
                    task_id=row.task_id,
                    event_type="execution.finished",
                    payload={
                        "execution_id": row.id,
                        "action": row.action,
                        "status": status.value,
                        "result_state": row.result_state,
                    },
                )
            )
            session.flush()
            return self._execution_record(row)

    def cancel_queued_execution(self, execution_id: str) -> ExecutionRecord:
        with self._session() as session:
            row = session.get(ExecutionRow, execution_id)
            if row is None:
                raise EntityNotFoundError(
                    f"execution {execution_id} was not found"
                )
            if row.status != ExecutionStatus.QUEUED.value:
                raise ValueError("only a queued execution can be cancelled")
            row.status = ExecutionStatus.CANCELLED.value
            row.active_task_id = None
            row.finished_at = utc_now()
            session.add(
                EventRow(
                    task_id=row.task_id,
                    event_type="execution.finished",
                    payload={
                        "execution_id": row.id,
                        "action": row.action,
                        "status": ExecutionStatus.CANCELLED.value,
                        "result_state": None,
                    },
                )
            )
            session.flush()
            return self._execution_record(row)

    def recover_running_executions(self) -> int:
        with self._session() as session:
            rows = session.scalars(
                select(ExecutionRow).where(
                    ExecutionRow.status == ExecutionStatus.RUNNING.value
                )
            ).all()
            for row in rows:
                row.status = ExecutionStatus.QUEUED.value
                row.started_at = None
                row.error_message = "worker restarted before execution completed"
                session.add(
                    EventRow(
                        task_id=row.task_id,
                        event_type="execution.recovered",
                        payload={"execution_id": row.id, "attempt": row.attempt},
                    )
                )
            return len(rows)

    def requeue_running_execution(self, execution_id: str) -> None:
        with self._session() as session:
            row = session.get(ExecutionRow, execution_id)
            if row is None or row.status != ExecutionStatus.RUNNING.value:
                return
            row.status = ExecutionStatus.QUEUED.value
            row.started_at = None
            row.error_message = "worker stopped before execution completed"
            session.add(
                EventRow(
                    task_id=row.task_id,
                    event_type="execution.requeued",
                    payload={"execution_id": row.id, "attempt": row.attempt},
                )
            )

    def start_tool_call(
        self,
        *,
        task_id: str,
        agent_name: str,
        tool_name: str,
        input_data: dict,
    ) -> ToolCallRecord:
        with self._session() as session:
            if session.get(TaskRow, task_id) is None:
                raise EntityNotFoundError(f"task {task_id} was not found")
            row = ToolCallRow(
                id=str(uuid4()),
                task_id=task_id,
                agent_name=agent_name,
                tool_name=tool_name,
                status=ToolCallStatus.RUNNING.value,
                input=input_data,
            )
            session.add(row)
            session.flush()
            session.add(
                EventRow(
                    task_id=task_id,
                    event_type="tool.called",
                    payload={"tool_call_id": row.id, "tool_name": tool_name},
                )
            )
            return self._tool_call_record(row)

    def finish_tool_call(
        self,
        tool_call_id: str,
        *,
        status: ToolCallStatus,
        output: dict | None = None,
        error_message: str | None = None,
    ) -> ToolCallRecord:
        if status is ToolCallStatus.RUNNING:
            raise ValueError("a finished tool call cannot remain RUNNING")
        with self._session() as session:
            row = session.get(ToolCallRow, tool_call_id)
            if row is None:
                raise EntityNotFoundError(f"tool call {tool_call_id} was not found")
            row.status = status.value
            row.output = output
            row.error_message = error_message
            row.finished_at = utc_now()
            session.add(
                EventRow(
                    task_id=row.task_id,
                    event_type="tool.returned",
                    payload={
                        "tool_call_id": row.id,
                        "tool_name": row.tool_name,
                        "status": status.value,
                    },
                )
            )
            session.flush()
            return self._tool_call_record(row)

    def list_tool_calls(self, task_id: str) -> list[ToolCallRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(ToolCallRow)
                .where(ToolCallRow.task_id == task_id)
                .order_by(ToolCallRow.started_at, ToolCallRow.id)
            ).all()
            return [self._tool_call_record(row) for row in rows]

    def list_recent_tool_calls(
        self,
        task_id: str,
        *,
        limit: int = 200,
    ) -> list[ToolCallRecord]:
        with self._session() as session:
            rows = list(
                session.scalars(
                    select(ToolCallRow)
                    .where(ToolCallRow.task_id == task_id)
                    .order_by(ToolCallRow.started_at.desc(), ToolCallRow.id.desc())
                    .limit(limit)
                ).all()
            )
            rows.reverse()
            return [self._tool_call_record(row) for row in rows]

    def tool_call_count(self, task_id: str) -> int:
        with self._session() as session:
            return session.scalar(
                select(func.count()).select_from(ToolCallRow).where(
                    ToolCallRow.task_id == task_id
                )
            ) or 0

    def indexed_files(self, project_id: str) -> dict[str, IndexedFileRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(IndexedFileRow).where(IndexedFileRow.project_id == project_id)
            ).all()
            return {
                row.file_path: IndexedFileRecord(
                    project_id=row.project_id,
                    file_path=row.file_path,
                    content_hash=row.content_hash,
                    language=row.language,
                    indexed_at=row.indexed_at,
                )
                for row in rows
            }

    def replace_file_index(
        self,
        *,
        project_id: str,
        file_path: str,
        content_hash: str,
        language: str,
        chunks: list[CodeChunk],
    ) -> None:
        with self._session() as session:
            if session.get(ProjectRow, project_id) is None:
                raise EntityNotFoundError(f"project {project_id} was not found")
            session.execute(
                delete(CodeChunkRow).where(
                    CodeChunkRow.project_id == project_id,
                    CodeChunkRow.file_path == file_path,
                )
            )
            indexed_file = session.get(IndexedFileRow, (project_id, file_path))
            if indexed_file is None:
                indexed_file = IndexedFileRow(
                    project_id=project_id,
                    file_path=file_path,
                    content_hash=content_hash,
                    language=language,
                )
                session.add(indexed_file)
            else:
                indexed_file.content_hash = content_hash
                indexed_file.language = language
                indexed_file.indexed_at = utc_now()
            for chunk in chunks:
                session.add(
                    CodeChunkRow(
                        id=chunk.id,
                        project_id=chunk.project_id,
                        file_path=chunk.file_path,
                        content_hash=chunk.content_hash,
                        chunk_index=chunk.chunk_index,
                        language=chunk.language,
                        symbol_name=chunk.symbol_name,
                        symbol_type=chunk.symbol_type,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                        embedding=chunk.embedding,
                    )
                )

    def delete_file_index(self, project_id: str, file_path: str) -> None:
        with self._session() as session:
            session.execute(
                delete(CodeChunkRow).where(
                    CodeChunkRow.project_id == project_id,
                    CodeChunkRow.file_path == file_path,
                )
            )
            session.execute(
                delete(IndexedFileRow).where(
                    IndexedFileRow.project_id == project_id,
                    IndexedFileRow.file_path == file_path,
                )
            )

    def code_chunks(self, project_id: str) -> list[CodeChunk]:
        with self._session() as session:
            rows = session.scalars(
                select(CodeChunkRow)
                .where(CodeChunkRow.project_id == project_id)
                .order_by(CodeChunkRow.file_path, CodeChunkRow.chunk_index)
            ).all()
            return [
                CodeChunk(
                    id=row.id,
                    project_id=row.project_id,
                    file_path=row.file_path,
                    content_hash=row.content_hash,
                    chunk_index=row.chunk_index,
                    language=row.language,
                    symbol_name=row.symbol_name,
                    symbol_type=row.symbol_type,
                    start_line=row.start_line,
                    end_line=row.end_line,
                    content=row.content,
                    embedding=row.embedding,
                )
                for row in rows
            ]

    def index_stats(self, project_id: str) -> IndexStats:
        with self._session() as session:
            indexed_files = session.scalar(
                select(func.count()).select_from(IndexedFileRow).where(
                    IndexedFileRow.project_id == project_id
                )
            ) or 0
            chunks = session.scalar(
                select(func.count()).select_from(CodeChunkRow).where(
                    CodeChunkRow.project_id == project_id
                )
            ) or 0
            language_rows = session.execute(
                select(CodeChunkRow.language, func.count())
                .where(CodeChunkRow.project_id == project_id)
                .group_by(CodeChunkRow.language)
            ).all()
            return IndexStats(
                project_id=project_id,
                indexed_files=indexed_files,
                chunks=chunks,
                languages={language: count for language, count in language_rows},
            )

    def upsert_memory(
        self,
        *,
        project_id: str,
        command: MemoryCreate,
        fingerprint: str,
        embedding: list[float],
    ) -> tuple[MemoryRecord, bool]:
        with self._session() as session:
            if session.get(ProjectRow, project_id) is None:
                raise EntityNotFoundError(f"project {project_id} was not found")
            if command.task_id:
                task = session.get(TaskRow, command.task_id)
                if task is None or task.project_id != project_id:
                    raise EntityNotFoundError(
                        f"task {command.task_id} was not found in project {project_id}"
                    )
            row = session.scalar(
                select(MemoryRow).where(
                    MemoryRow.project_id == project_id,
                    MemoryRow.fingerprint == fingerprint,
                )
            )
            created = row is None
            if row is None:
                row = MemoryRow(
                    id=str(uuid4()),
                    project_id=project_id,
                    task_id=command.task_id,
                    type=command.type.value,
                    status=command.status.value,
                    category=command.category,
                    summary=command.summary,
                    content=command.content,
                    source_type=command.source_type,
                    source_id=command.source_id,
                    source_revision=command.source_revision,
                    confidence=command.confidence,
                    metadata_json=command.metadata,
                    fingerprint=fingerprint,
                    embedding=embedding,
                )
                session.add(row)
            else:
                row.source_id = command.source_id or row.source_id
                row.source_revision = command.source_revision or row.source_revision
                row.confidence = max(row.confidence, command.confidence)
                if row.status == MemoryStatus.CANDIDATE.value:
                    row.status = command.status.value
                row.metadata_json = {**row.metadata_json, **command.metadata}
                row.updated_at = utc_now()
            session.flush()
            return self._memory_record(row), created

    def get_memory(self, memory_id: str) -> MemoryRecord:
        with self._session() as session:
            row = session.get(MemoryRow, memory_id)
            if row is None:
                raise EntityNotFoundError(f"memory {memory_id} was not found")
            return self._memory_record(row)

    def list_memories(
        self,
        project_id: str,
        *,
        types: list[MemoryType] | None = None,
        statuses: list[MemoryStatus] | None = None,
        task_id: str | None = None,
        categories: list[str] | None = None,
    ) -> list[MemoryRecord]:
        with self._session() as session:
            if session.get(ProjectRow, project_id) is None:
                raise EntityNotFoundError(f"project {project_id} was not found")
            query = select(MemoryRow).where(MemoryRow.project_id == project_id)
            if types:
                query = query.where(MemoryRow.type.in_([item.value for item in types]))
            if statuses:
                query = query.where(
                    MemoryRow.status.in_([item.value for item in statuses])
                )
            if task_id:
                query = query.where(MemoryRow.task_id == task_id)
            if categories:
                query = query.where(MemoryRow.category.in_(categories))
            rows = session.scalars(query.order_by(MemoryRow.updated_at.desc())).all()
            return [self._memory_record(row) for row in rows]

    def update_memory_status(
        self, memory_id: str, status: MemoryStatus
    ) -> MemoryRecord:
        with self._session() as session:
            row = session.get(MemoryRow, memory_id)
            if row is None:
                raise EntityNotFoundError(f"memory {memory_id} was not found")
            row.status = status.value
            row.updated_at = utc_now()
            session.flush()
            return self._memory_record(row)

    def mark_revision_stale(self, project_id: str, current_revision: str) -> int:
        with self._session() as session:
            result = session.execute(
                update(MemoryRow)
                .where(
                    MemoryRow.project_id == project_id,
                    MemoryRow.type.in_(
                        [MemoryType.PROJECT.value, MemoryType.LONG_TERM.value]
                    ),
                    MemoryRow.status.in_(
                        [MemoryStatus.CANDIDATE.value, MemoryStatus.VERIFIED.value]
                    ),
                    MemoryRow.source_revision.is_not(None),
                    MemoryRow.source_revision != current_revision,
                )
                .values(status=MemoryStatus.STALE.value, updated_at=utc_now())
            )
            return result.rowcount or 0

    def mark_task_short_term_stale(self, task_id: str) -> int:
        with self._session() as session:
            result = session.execute(
                update(MemoryRow)
                .where(
                    MemoryRow.task_id == task_id,
                    MemoryRow.type == MemoryType.SHORT_TERM.value,
                    MemoryRow.status.in_(
                        [MemoryStatus.CANDIDATE.value, MemoryStatus.VERIFIED.value]
                    ),
                )
                .values(status=MemoryStatus.STALE.value, updated_at=utc_now())
            )
            return result.rowcount or 0

    @staticmethod
    def _task_record(row: TaskRow) -> TaskRecord:
        assessed_at = row.policy.assessed_at if row.policy is not None else None
        if assessed_at is not None and assessed_at.tzinfo is None:
            # SQLite 不保存时区偏移；策略时间统一按 UTC 恢复，保证创建与读取一致。
            assessed_at = assessed_at.replace(tzinfo=timezone.utc)
        policy = (
            TaskPolicyDecision(
                execution_scope=ExecutionScope(row.policy.execution_scope),
                preference=DeliveryPreference(row.policy.preference),
                risk_score=row.policy.risk_score,
                governance_level=row.policy.governance_level,
                reasons=list(row.policy.reasons),
                hard_risk_flags=list(row.policy.hard_risk_flags),
                assessed_at=assessed_at,
            )
            if row.policy is not None
            else None
        )
        return TaskRecord(
            id=row.id,
            title=build_task_title(row.requirement),
            project_id=row.project_id,
            requirement=row.requirement,
            state=TaskState(row.state),
            state_version=row.state_version,
            error_message=row.error_message,
            policy=policy,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _task_policy_row(
        task_id: str,
        policy: TaskPolicyDecision,
    ) -> TaskPolicyRow:
        return TaskPolicyRow(
            task_id=task_id,
            execution_scope=policy.execution_scope.value,
            preference=policy.preference.value,
            risk_score=policy.risk_score,
            governance_level=policy.governance_level.value,
            reasons=policy.reasons,
            hard_risk_flags=policy.hard_risk_flags,
            assessed_at=policy.assessed_at,
        )

    @staticmethod
    def _evaluation_feedback_record(
        row: TaskEvaluationFeedbackRow,
    ) -> TaskEvaluationFeedbackRecord:
        return TaskEvaluationFeedbackRecord(
            task_id=row.task_id,
            rating=row.rating,
            accepted=row.accepted,
            comment=row.comment,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _artifact_record(row: ArtifactRow) -> ArtifactRecord:
        return ArtifactRecord(
            id=row.id,
            task_id=row.task_id,
            type=ArtifactType(row.type),
            version=row.version,
            created_by=row.created_by,
            content=row.content,
            created_at=row.created_at,
        )

    @staticmethod
    def _tool_call_record(row: ToolCallRow) -> ToolCallRecord:
        return ToolCallRecord(
            id=row.id,
            task_id=row.task_id,
            agent_name=row.agent_name,
            tool_name=row.tool_name,
            status=ToolCallStatus(row.status),
            input=row.input,
            output=row.output,
            error_message=row.error_message,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @staticmethod
    def _memory_record(row: MemoryRow) -> MemoryRecord:
        return MemoryRecord(
            id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            type=MemoryType(row.type),
            status=MemoryStatus(row.status),
            category=row.category,
            summary=row.summary,
            content=row.content,
            source_type=row.source_type,
            source_id=row.source_id,
            source_revision=row.source_revision,
            confidence=row.confidence,
            metadata=row.metadata_json,
            fingerprint=row.fingerprint,
            embedding=row.embedding,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _execution_record(row: ExecutionRow) -> ExecutionRecord:
        return ExecutionRecord(
            id=row.id,
            task_id=row.task_id,
            action=ExecutionAction(row.action),
            payload=row.payload,
            status=ExecutionStatus(row.status),
            attempt=row.attempt,
            result_state=TaskState(row.result_state) if row.result_state else None,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
