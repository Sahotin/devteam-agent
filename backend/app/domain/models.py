from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.enums import (
    ArtifactType,
    DeliveryPreference,
    ExecutionScope,
    ExecutionAction,
    ExecutionStatus,
    IterationKind,
    TaskState,
    ToolCallStatus,
)
from backend.app.domain.task_policy import TaskPolicyDecision


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1)
    summary: str = ""


class ProjectRecord(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class ProjectWorkspaceUpdate(BaseModel):
    """用户确认后更新项目的实际代码目录，不会移动或复制任何文件。"""

    root_path: str = Field(min_length=1)


class WorkspaceAccessReport(BaseModel):
    root_path: str
    writable: bool
    message: str


class TaskCreate(BaseModel):
    project_id: str
    requirement: str = Field(min_length=5)
    execution_scope: ExecutionScope = ExecutionScope.AUTO
    preference: DeliveryPreference = DeliveryPreference.BALANCED


class TaskIterationCreate(BaseModel):
    kind: IterationKind
    request: str = Field(min_length=5, max_length=8000)
    execution_scope: ExecutionScope = ExecutionScope.AUTO
    preference: DeliveryPreference = DeliveryPreference.BALANCED


class TaskRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    project_id: str
    requirement: str
    state: TaskState
    state_version: int
    error_message: str | None = None
    policy: TaskPolicyDecision | None = None
    created_at: datetime
    updated_at: datetime

class ArtifactRecord(BaseModel):
    id: str
    task_id: str
    type: ArtifactType
    version: int
    created_by: str
    content: dict
    created_at: datetime


class EventRecord(BaseModel):
    id: int
    task_id: str
    event_type: str
    payload: dict
    created_at: datetime


class ToolCallRecord(BaseModel):
    id: str
    task_id: str
    agent_name: str
    tool_name: str
    status: ToolCallStatus
    input: dict
    output: dict | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class CheckpointRecord(BaseModel):
    id: int
    task_id: str
    state: TaskState
    state_version: int
    snapshot: dict
    created_at: datetime


class ExecutionRecord(BaseModel):
    id: str
    task_id: str
    action: ExecutionAction
    payload: dict
    status: ExecutionStatus
    attempt: int
    result_state: TaskState | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
