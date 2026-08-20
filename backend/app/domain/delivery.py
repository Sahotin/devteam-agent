from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DeliveryCommand(BaseModel):
    label: str
    command: str
    description: str


class ProjectFileInfo(BaseModel):
    path: str
    kind: str
    description: str


class DeliveryGuide(BaseModel):
    project_name: str
    root_path: str
    project_type: str
    summary: str
    entry_point: str | None = None
    start_commands: list[DeliveryCommand]
    structure: list[ProjectFileInfo]
    operation_steps: list[str]
    notes: list[str]


class RuntimeState(StrEnum):
    STOPPED = "STOPPED"
    DEPENDENCY_REQUIRED = "DEPENDENCY_REQUIRED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"


class DependencyScope(StrEnum):
    SYSTEM = "SYSTEM"
    PROJECT = "PROJECT"


class RuntimeDependency(BaseModel):
    id: str
    name: str
    description: str
    scope: DependencyScope
    command: str
    automatic: bool = True
    requires_admin: bool = False


class DependencyInstallRequest(BaseModel):
    confirmed: bool
    dependency_ids: list[str] = Field(min_length=1)


class ProjectRuntime(BaseModel):
    task_id: str
    state: RuntimeState
    project_type: str
    command: str | None = None
    pid: int | None = None
    url: str | None = None
    health_checked: bool = False
    message: str
    logs: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    exit_code: int | None = None
    dependencies: list[RuntimeDependency] = Field(default_factory=list)
