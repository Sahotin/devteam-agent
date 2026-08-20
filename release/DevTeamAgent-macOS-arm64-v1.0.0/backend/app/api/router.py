from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from backend.app.api.dependencies import get_container
from backend.app.api.schemas import (
    ArchitectureDecisionRequest,
    CapabilityResponse,
    ExecutionRequest,
    GitCommitRequest,
    PrdDecisionRequest,
    TaskControlRequest,
    TaskObservabilityResponse,
)
from backend.app.tools.git_tools import GitDiffOutput, GitLogOutput, GitStatusOutput
from backend.app.domain.rag import (
    CodeSearchQuery,
    CodeSearchResults,
    IndexReport,
    IndexStats,
)
from backend.app.domain.enums import MemoryStatus, MemoryType
from backend.app.domain.delivery import DeliveryGuide
from backend.app.domain.memory import (
    MemoryConsolidationReport,
    MemoryCreate,
    MemoryRecord,
    MemoryRevisionRequest,
    MemorySearchQuery,
    MemorySearchResults,
    MemoryVerificationRequest,
)
from backend.app.container import ApplicationContainer
from backend.app.core.version import VERSION
from backend.app.domain.models import (
    ArtifactRecord,
    CheckpointRecord,
    EventRecord,
    ExecutionRecord,
    ProjectCreate,
    ProjectRecord,
    TaskCreate,
    TaskIterationCreate,
    TaskRecord,
    ToolCallRecord,
)


router = APIRouter(prefix="/api/v1")
Container = Annotated[ApplicationContainer, Depends(get_container)]


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def readiness(container: Container) -> dict[str, str]:
    database_ready = container.repository.ping()
    worker_ready = container.execution_manager.is_running
    if not database_ready or not worker_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "database": "ready" if database_ready else "unavailable",
                "worker": "ready" if worker_ready else "unavailable",
            },
        )
    return {"status": "ready"}


@router.get("/metrics", response_class=PlainTextResponse, tags=["system"])
def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        request.app.state.metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get(
    "/capabilities",
    response_model=CapabilityResponse,
    tags=["system"],
)
def capabilities(container: Container) -> CapabilityResponse:
    settings = container.settings
    git_configured = bool(
        (settings.git_executable and Path(settings.git_executable).is_file())
        or (settings.git_executable and shutil.which(settings.git_executable))
        or shutil.which("git")
    )
    docker_configured = bool(
        (settings.docker_executable and Path(settings.docker_executable).is_file())
        or (settings.docker_executable and shutil.which(settings.docker_executable))
        or shutil.which("docker")
    )
    limitations: list[str] = []
    if settings.terminal_executor == "local":
        limitations.append("本地执行器不提供容器级网络和文件系统隔离")
    if settings.terminal_executor == "docker" and not docker_configured:
        limitations.append("已选择 Docker 执行器，但当前未找到 Docker 可执行文件")
    if not git_configured:
        limitations.append("当前未找到 Git 可执行文件")
    return CapabilityResponse(
        version=VERSION,
        terminal_executor=settings.terminal_executor,
        worker_concurrency=settings.worker_concurrency,
        async_execution=True,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        git_configured=git_configured,
        docker_configured=docker_configured,
        limitations=limitations,
    )


@router.post(
    "/projects",
    response_model=ProjectRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
def create_project(command: ProjectCreate, container: Container) -> ProjectRecord:
    return container.repository.create_project(command)


@router.get("/projects", response_model=list[ProjectRecord], tags=["projects"])
def list_projects(container: Container) -> list[ProjectRecord]:
    return container.repository.list_projects()


@router.get("/projects/{project_id}", response_model=ProjectRecord, tags=["projects"])
def get_project(project_id: str, container: Container) -> ProjectRecord:
    return container.repository.get_project(project_id)


@router.post(
    "/tasks",
    response_model=TaskRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["tasks"],
)
def create_task(command: TaskCreate, container: Container) -> TaskRecord:
    return container.repository.create_task(command)


@router.post(
    "/tasks/{task_id}/iterations",
    response_model=TaskRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["tasks"],
)
def create_task_iteration(
    task_id: str,
    command: TaskIterationCreate,
    container: Container,
) -> TaskRecord:
    source = container.repository.get_task(task_id)
    created = container.repository.create_iteration_task(task_id, command)
    kind_labels = {
        "BUG_FIX": "故障修复",
        "REQUIREMENT_CHANGE": "需求变更",
        "OPTIMIZATION": "体验优化",
    }
    container.memory_service.create_memory(
        source.project_id,
        MemoryCreate(
            task_id=created.id,
            type=MemoryType.SHORT_TERM,
            status=MemoryStatus.VERIFIED,
            category="iteration_context",
            summary=f"{kind_labels[command.kind.value]}：{command.request[:200]}",
            content=(
                "这是对已有项目的持续迭代，必须在同一个代码仓库中理解并修改现有实现。\n"
                f"来源任务：{source.id}\n"
                f"上一轮需求：{source.requirement}\n"
                f"迭代类型：{kind_labels[command.kind.value]}\n"
                f"本轮用户反馈：{command.request}"
            ),
            source_type="task_iteration",
            source_id=source.id,
            source_revision=str(source.state_version),
            confidence=1.0,
            metadata={
                "parent_task_id": source.id,
                "iteration_kind": command.kind.value,
            },
        ),
    )
    return created


@router.get("/tasks", response_model=list[TaskRecord], tags=["tasks"])
def list_tasks(
    container: Container, project_id: str | None = None
) -> list[TaskRecord]:
    return container.repository.list_tasks(project_id)


@router.get("/tasks/{task_id}", response_model=TaskRecord, tags=["tasks"])
def get_task(task_id: str, container: Container) -> TaskRecord:
    return container.repository.get_task(task_id)


@router.get(
    "/tasks/{task_id}/checkpoint",
    response_model=CheckpointRecord,
    tags=["tasks"],
)
def get_latest_checkpoint(
    task_id: str, container: Container
) -> CheckpointRecord:
    container.repository.get_task(task_id)
    return container.repository.latest_checkpoint(task_id)


@router.post("/tasks/{task_id}/start", response_model=TaskRecord, tags=["workflow"])
async def start_task(task_id: str, container: Container) -> TaskRecord:
    return await container.workflow.start(task_id)


@router.post("/tasks/{task_id}/pause", response_model=TaskRecord, tags=["workflow"])
def pause_task(
    task_id: str, command: TaskControlRequest, container: Container
) -> TaskRecord:
    return container.workflow.pause(task_id, reason=command.reason)


@router.post("/tasks/{task_id}/resume", response_model=TaskRecord, tags=["workflow"])
def resume_task(task_id: str, container: Container) -> TaskRecord:
    return container.workflow.resume(task_id)


@router.post("/tasks/{task_id}/retry", response_model=TaskRecord, tags=["workflow"])
def retry_failed_task(task_id: str, container: Container) -> TaskRecord:
    return container.workflow.retry_failed(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=TaskRecord, tags=["workflow"])
def cancel_task(
    task_id: str, command: TaskControlRequest, container: Container
) -> TaskRecord:
    return container.workflow.cancel(task_id, reason=command.reason)


@router.post(
    "/tasks/{task_id}/prd-decision",
    response_model=TaskRecord,
    tags=["workflow"],
)
async def decide_prd(
    task_id: str,
    command: PrdDecisionRequest,
    container: Container,
) -> TaskRecord:
    return await container.workflow.decide_prd(
        task_id,
        command.decision,
        feedback=command.feedback,
    )


@router.post(
    "/tasks/{task_id}/architecture-decision",
    response_model=TaskRecord,
    tags=["workflow"],
)
async def decide_architecture(
    task_id: str,
    command: ArchitectureDecisionRequest,
    container: Container,
) -> TaskRecord:
    return await container.workflow.decide_architecture(
        task_id,
        command.decision,
        feedback=command.feedback,
        selected_option_id=command.selected_option_id,
        autonomous=command.autonomous,
    )


@router.get(
    "/tasks/{task_id}/delivery-guide",
    response_model=DeliveryGuide,
    tags=["delivery"],
)
def get_delivery_guide(
    task_id: str, container: Container
) -> DeliveryGuide:
    from backend.app.delivery.service import DeliveryGuideService

    return DeliveryGuideService(container.repository).build(task_id)


@router.post(
    "/tasks/{task_id}/diagnosis",
    response_model=ArtifactRecord,
    tags=["workflow"],
)
async def diagnose_task_issue(
    task_id: str,
    container: Container,
) -> ArtifactRecord:
    return await container.workflow.diagnose_issue(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=TaskRecord,
    tags=["workflow"],
)
async def run_review(task_id: str, container: Container) -> TaskRecord:
    return await container.workflow.run_review(task_id)


@router.post(
    "/tasks/{task_id}/test",
    response_model=TaskRecord,
    tags=["workflow"],
)
async def run_tests(task_id: str, container: Container) -> TaskRecord:
    return await container.workflow.run_tests(task_id)


@router.get(
    "/tasks/{task_id}/artifacts",
    response_model=list[ArtifactRecord],
    tags=["artifacts"],
)
def list_artifacts(task_id: str, container: Container) -> list[ArtifactRecord]:
    container.repository.get_task(task_id)
    return container.repository.list_artifacts(task_id)


@router.get(
    "/tasks/{task_id}/events",
    response_model=list[EventRecord],
    tags=["events"],
)
def list_events(task_id: str, container: Container) -> list[EventRecord]:
    container.repository.get_task(task_id)
    return container.repository.list_events(task_id)


@router.get(
    "/tasks/{task_id}/event-stream",
    tags=["observability"],
)
async def stream_events(
    task_id: str,
    request: Request,
    container: Container,
    after_event_id: int = 0,
    follow: bool = True,
) -> StreamingResponse:
    container.repository.get_task(task_id)

    async def generate_events():
        cursor = after_event_id
        idle_ticks = 0
        while True:
            events = container.repository.list_events_after(task_id, cursor)
            for event in events:
                cursor = event.id
                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield (
                    f"id: {event.id}\n"
                    f"event: {event.event_type}\n"
                    f"data: {payload}\n\n"
                )
            if not follow or await request.is_disconnected():
                return
            idle_ticks += 1
            if idle_ticks >= 20:
                idle_ticks = 0
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/tasks/{task_id}/tool-calls",
    response_model=list[ToolCallRecord],
    tags=["tools"],
)
def list_tool_calls(task_id: str, container: Container) -> list[ToolCallRecord]:
    container.repository.get_task(task_id)
    return container.repository.list_tool_calls(task_id)


@router.get(
    "/tasks/{task_id}/git/status",
    response_model=GitStatusOutput,
    tags=["git"],
)
async def git_status(task_id: str, container: Container) -> GitStatusOutput:
    return await container.workflow.git_status(task_id)


@router.get(
    "/tasks/{task_id}/git/diff",
    response_model=GitDiffOutput,
    tags=["git"],
)
async def git_diff(task_id: str, container: Container) -> GitDiffOutput:
    return await container.workflow.git_diff(task_id)


@router.get(
    "/tasks/{task_id}/git/log",
    response_model=GitLogOutput,
    tags=["git"],
)
async def git_log(
    task_id: str, container: Container, limit: int = 10
) -> GitLogOutput:
    return await container.workflow.git_log(task_id, limit=limit)


@router.post(
    "/tasks/{task_id}/git/commit",
    response_model=ArtifactRecord,
    tags=["git"],
)
async def git_commit(
    task_id: str, command: GitCommitRequest, container: Container
) -> ArtifactRecord:
    return await container.workflow.commit_task(task_id, command.message)


@router.post(
    "/projects/{project_id}/index",
    response_model=IndexReport,
    tags=["rag"],
)
def index_project(project_id: str, container: Container) -> IndexReport:
    return container.index_service.index_project(project_id)


@router.get(
    "/projects/{project_id}/index/stats",
    response_model=IndexStats,
    tags=["rag"],
)
def index_stats(project_id: str, container: Container) -> IndexStats:
    return container.index_service.stats(project_id)


@router.post(
    "/projects/{project_id}/search",
    response_model=CodeSearchResults,
    tags=["rag"],
)
def search_project(
    project_id: str,
    query: CodeSearchQuery,
    container: Container,
) -> CodeSearchResults:
    return container.index_service.search(project_id, query)


@router.post(
    "/projects/{project_id}/memories",
    response_model=MemoryRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["memory"],
)
def create_memory(
    project_id: str, command: MemoryCreate, container: Container
) -> MemoryRecord:
    memory, _ = container.memory_service.create_memory(project_id, command)
    return memory


@router.get(
    "/projects/{project_id}/memories",
    response_model=list[MemoryRecord],
    tags=["memory"],
)
def list_memories(project_id: str, container: Container) -> list[MemoryRecord]:
    return container.repository.list_memories(project_id)


@router.post(
    "/projects/{project_id}/memories/search",
    response_model=MemorySearchResults,
    tags=["memory"],
)
def search_memories(
    project_id: str, query: MemorySearchQuery, container: Container
) -> MemorySearchResults:
    return container.memory_service.search(project_id, query)


@router.patch(
    "/memories/{memory_id}/status",
    response_model=MemoryRecord,
    tags=["memory"],
)
def update_memory_status(
    memory_id: str,
    command: MemoryVerificationRequest,
    container: Container,
) -> MemoryRecord:
    return container.memory_service.verify(memory_id, command.status)


@router.post(
    "/projects/{project_id}/memories/reconcile-revision",
    tags=["memory"],
)
def reconcile_memory_revision(
    project_id: str,
    command: MemoryRevisionRequest,
    container: Container,
) -> dict[str, int | str]:
    stale_count = container.memory_service.reconcile_revision(
        project_id, command.current_revision
    )
    return {"project_id": project_id, "stale_count": stale_count}


@router.get(
    "/tasks/{task_id}/short-term-memory",
    response_model=list[MemoryRecord],
    tags=["memory"],
)
def list_short_term_memory(
    task_id: str, container: Container
) -> list[MemoryRecord]:
    task = container.repository.get_task(task_id)
    return container.repository.list_memories(
        task.project_id,
        types=[MemoryType.SHORT_TERM],
        statuses=list(MemoryStatus),
        task_id=task_id,
    )


@router.post(
    "/tasks/{task_id}/memory/consolidate",
    response_model=MemoryConsolidationReport,
    tags=["memory"],
)
def consolidate_memory(
    task_id: str, container: Container
) -> MemoryConsolidationReport:
    return container.memory_service.consolidate_task(task_id)


@router.post(
    "/tasks/{task_id}/executions",
    response_model=ExecutionRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["executions"],
)
async def enqueue_execution(
    task_id: str,
    command: ExecutionRequest,
    container: Container,
) -> ExecutionRecord:
    return await container.execution_manager.enqueue(
        task_id,
        command.action,
        command.execution_payload(),
    )


@router.get(
    "/tasks/{task_id}/executions",
    response_model=list[ExecutionRecord],
    tags=["executions"],
)
def list_executions(
    task_id: str, container: Container
) -> list[ExecutionRecord]:
    return container.repository.list_executions(task_id)


@router.get(
    "/executions/{execution_id}",
    response_model=ExecutionRecord,
    tags=["executions"],
)
def get_execution(
    execution_id: str, container: Container
) -> ExecutionRecord:
    return container.repository.get_execution(execution_id)


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=ExecutionRecord,
    tags=["executions"],
)
def cancel_execution(
    execution_id: str, container: Container
) -> ExecutionRecord:
    return container.execution_manager.cancel(execution_id)


@router.get(
    "/tasks/{task_id}/observability",
    response_model=TaskObservabilityResponse,
    tags=["observability"],
)
def task_observability(
    task_id: str, container: Container
) -> TaskObservabilityResponse:
    task = container.repository.get_task(task_id)
    return TaskObservabilityResponse(
        task=task,
        executions=container.repository.list_executions(task_id),
        artifacts=container.repository.list_artifacts(task_id),
        tool_calls=container.repository.list_tool_calls(task_id),
        event_count=container.repository.event_count(task_id),
    )
