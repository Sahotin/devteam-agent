from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.app.domain.artifacts import (
    AcceptanceCriterion,
    ArchitectureArtifact,
    ArchitectureDecision,
    CodeChangeArtifact,
    FileChange,
    PRDArtifact,
    ReviewArtifact,
    ReviewIssue,
    TestCommandResult,
    TestReportArtifact,
    UIUXArtifact,
    RequirementItem,
)
from backend.app.domain.enums import (
    ArtifactType,
    CommandStatus,
    ExecutionStatus,
    ReviewSeverity,
)
from backend.app.domain.models import ExecutionRecord, ProjectRecord, TaskRecord
from backend.app.domain.rag import IndexStats
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.tools.base import BaseTool, ToolContext


class ProjectContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactManifestItem(BaseModel):
    id: str
    type: ArtifactType
    version: int
    created_by: str
    created_at: datetime
    summary: str


class ArchitectureContext(BaseModel):
    overview: str
    selected_option_id: str | None
    decisions: list[ArchitectureDecision]
    risks: list[str]


class UIContext(BaseModel):
    selected_option_id: str | None
    quality_criteria: list[str]


class CodeChangeContext(BaseModel):
    summary: str
    changes: list[FileChange]
    unresolved_issues: list[str]


class ReviewContext(BaseModel):
    verdict: str
    summary: str
    blocking_issues: list[ReviewIssue]


class TestContext(BaseModel):
    verdict: str
    summary: str
    failed_commands: list[TestCommandResult]
    limitations: list[str]


class ProjectContextOutput(BaseModel):
    project: ProjectRecord
    task: TaskRecord
    active_execution: ExecutionRecord | None
    requirements: list[RequirementItem]
    acceptance_criteria: list[AcceptanceCriterion]
    architecture: ArchitectureContext | None
    ui_design: UIContext | None
    latest_code_change: CodeChangeContext | None
    latest_review: ReviewContext | None
    latest_test: TestContext | None
    artifact_manifest: list[ArtifactManifestItem]
    index_stats: IndexStats


class ProjectContextTool(BaseTool):
    name = "project.context"
    description = "读取当前任务、项目、最新产物、活动执行和代码索引摘要"
    required_permission = "project:context"
    input_model = ProjectContextInput
    output_model = ProjectContextOutput

    def __init__(self, repository: SqlAlchemyRepository) -> None:
        self._repository = repository

    async def execute(
        self, context: ToolContext, input_data: ProjectContextInput
    ) -> ProjectContextOutput:
        del input_data
        task = self._repository.get_task(context.task_id)
        project = self._repository.get_project(task.project_id)
        artifacts = self._repository.list_artifacts(task.id)
        latest = {artifact.type: artifact for artifact in artifacts}
        executions = self._repository.list_executions(task.id)
        active_execution = next(
            (
                execution
                for execution in reversed(executions)
                if execution.status
                in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}
            ),
            None,
        )

        prd = self._parse(latest, ArtifactType.PRD, PRDArtifact)
        architecture = self._parse(
            latest, ArtifactType.ARCHITECTURE, ArchitectureArtifact
        )
        ui_design = self._parse(latest, ArtifactType.UI_DESIGN, UIUXArtifact)
        code_change = self._parse(
            latest, ArtifactType.CODE_CHANGE, CodeChangeArtifact
        )
        review = self._parse(latest, ArtifactType.REVIEW, ReviewArtifact)
        test_report = self._parse(
            latest, ArtifactType.TEST_REPORT, TestReportArtifact
        )

        return ProjectContextOutput(
            project=project,
            task=task,
            active_execution=active_execution,
            requirements=prd.requirements if prd else [],
            acceptance_criteria=prd.acceptance_criteria if prd else [],
            architecture=(
                ArchitectureContext(
                    overview=architecture.overview,
                    selected_option_id=architecture.selected_option_id,
                    decisions=architecture.decisions,
                    risks=architecture.risks,
                )
                if architecture
                else None
            ),
            ui_design=(
                UIContext(
                    selected_option_id=ui_design.selected_option_id,
                    quality_criteria=ui_design.quality_criteria,
                )
                if ui_design
                else None
            ),
            latest_code_change=(
                CodeChangeContext(
                    summary=code_change.summary,
                    changes=code_change.changes,
                    unresolved_issues=code_change.unresolved_issues,
                )
                if code_change
                else None
            ),
            latest_review=(
                ReviewContext(
                    verdict=review.verdict.value,
                    summary=review.summary,
                    blocking_issues=[
                        issue
                        for issue in review.issues
                        if issue.severity
                        in {ReviewSeverity.BLOCKER, ReviewSeverity.MAJOR}
                    ],
                )
                if review
                else None
            ),
            latest_test=(
                TestContext(
                    verdict=test_report.verdict.value,
                    summary=test_report.summary,
                    failed_commands=[
                        result
                        for result in test_report.results
                        if result.status is not CommandStatus.SUCCEEDED
                    ],
                    limitations=test_report.limitations,
                )
                if test_report
                else None
            ),
            artifact_manifest=[
                ArtifactManifestItem(
                    id=artifact.id,
                    type=artifact.type,
                    version=artifact.version,
                    created_by=artifact.created_by,
                    created_at=artifact.created_at,
                    summary=self._summary(artifact.content, artifact.type),
                )
                for artifact in latest.values()
            ],
            index_stats=self._repository.index_stats(project.id),
        )

    def audit_output(self, output: ProjectContextOutput) -> dict:
        return {
            "project_id": output.project.id,
            "task_state": output.task.state.value,
            "has_active_execution": output.active_execution is not None,
            "artifact_count": len(output.artifact_manifest),
            "artifact_types": [item.type.value for item in output.artifact_manifest],
            "requirement_count": len(output.requirements),
            "acceptance_criteria_count": len(output.acceptance_criteria),
            "indexed_files": output.index_stats.indexed_files,
            "content_redacted": True,
        }

    @staticmethod
    def _parse(latest: dict, artifact_type: ArtifactType, model_type):
        artifact = latest.get(artifact_type)
        return model_type.model_validate(artifact.content) if artifact else None

    @staticmethod
    def _summary(content: dict, artifact_type: ArtifactType) -> str:
        value = (
            content.get("summary")
            or content.get("title")
            or content.get("overview")
            or f"{artifact_type.value} 产物"
        )
        return str(value)[:500]
