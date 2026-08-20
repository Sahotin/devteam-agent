from __future__ import annotations

from backend.app.agents.architect import ArchitectAgent
from backend.app.agents.developer import DeveloperAgent
from backend.app.agents.diagnostic import DiagnosticAgent
from backend.app.agents.product import ProductAgent
from backend.app.agents.reviewer import ReviewerAgent
from backend.app.agents.tester import TesterAgent
from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    CodeChangeArtifact,
    DiagnosisArtifact,
    GitCommitArtifact,
    PRDArtifact,
    ReviewArtifact,
    TestReportArtifact,
)
from backend.app.domain.enums import (
    ApprovalDecision,
    ArtifactType,
    IterationKind,
    ReviewVerdict,
    TaskState,
    TestVerdict,
)
from backend.app.domain.models import ArtifactRecord, TaskRecord
from backend.app.domain.memory import MemorySearchQuery
from backend.app.domain.state_machine import PAUSABLE_STATES, TERMINAL_STATES
from backend.app.infrastructure.database.repository import SqlAlchemyRepository
from backend.app.infrastructure.database.repository import EntityNotFoundError
from backend.app.memory.service import MemoryService
from backend.app.tools.base import ToolContext
from backend.app.tools.git_tools import (
    GitCommitOutput,
    GitDiffOutput,
    GitLogOutput,
    GitStatusOutput,
)
from backend.app.tools.registry import ToolRegistry
from backend.app.execution.progress import ProgressReporter, report_progress


class WorkflowExecutionError(RuntimeError):
    pass


MAX_DEVELOPMENT_REVISIONS = 3
MAX_TEST_FAILURES = 2


class WorkflowService:
    def __init__(
        self,
        repository: SqlAlchemyRepository,
        product_agent: ProductAgent,
        diagnostic_agent: DiagnosticAgent,
        architect_agent: ArchitectAgent,
        developer_agent: DeveloperAgent,
        reviewer_agent: ReviewerAgent,
        tester_agent: TesterAgent,
        tools: ToolRegistry,
        memory_service: MemoryService,
    ) -> None:
        self._repository = repository
        self._product_agent = product_agent
        self._diagnostic_agent = diagnostic_agent
        self._architect_agent = architect_agent
        self._developer_agent = developer_agent
        self._reviewer_agent = reviewer_agent
        self._tester_agent = tester_agent
        self._tools = tools
        self._memory_service = memory_service

    async def start(
        self, task_id: str, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.CREATED:
            raise ValueError(
                f"task {task_id} cannot start from state {task.state.value}"
            )
        task = self._repository.transition_task(
            task.id,
            TaskState.REQUIREMENT_ANALYZING,
            expected_version=task.state_version,
        )
        try:
            return await self._run_product(task, progress=progress)
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Product Agent execution failed") from error

    def pause(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state not in PAUSABLE_STATES:
            raise ValueError(
                f"task {task_id} cannot pause from state {task.state.value}"
            )
        resume_state = task.state
        task = self._repository.transition_task(
            task.id,
            TaskState.PAUSED,
            expected_version=task.state_version,
            event_payload={"reason": reason or ""},
        )
        self._repository.save_checkpoint(
            task,
            {
                "task_id": task.id,
                "state": task.state.value,
                "state_version": task.state_version,
                "resume_state": resume_state.value,
                "reason": reason or "",
            },
        )
        return task

    def resume(self, task_id: str) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.PAUSED:
            raise ValueError(f"task {task_id} is not paused")
        checkpoint = self._repository.latest_checkpoint(task.id)
        resume_value = checkpoint.snapshot.get("resume_state")
        if not resume_value:
            raise ValueError(f"task {task_id} pause checkpoint has no resume state")
        resume_state = TaskState(resume_value)
        return self._repository.transition_task(
            task.id,
            resume_state,
            expected_version=task.state_version,
            event_payload={"checkpoint_id": checkpoint.id},
        )

    def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state in TERMINAL_STATES:
            raise ValueError(
                f"task {task_id} cannot cancel from terminal state {task.state.value}"
            )
        return self._repository.transition_task(
            task.id,
            TaskState.CANCELLED,
            expected_version=task.state_version,
            event_payload={"reason": reason or ""},
        )

    def retry_failed(self, task_id: str) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.FAILED:
            raise ValueError(f"task {task_id} is not failed")
        try:
            checkpoint = self._repository.latest_checkpoint(task.id)
            target = checkpoint.state
            checkpoint_id: int | None = checkpoint.id
        except EntityNotFoundError:
            target = TaskState.CREATED
            checkpoint_id = None
        recoverable = {
            TaskState.CREATED,
            TaskState.PRD_APPROVAL,
            TaskState.ARCH_APPROVAL,
            TaskState.REVIEWING,
            TaskState.TESTING,
        }
        if target not in recoverable:
            raise ValueError(
                f"checkpoint state {target.value} cannot be retried safely"
            )
        return self._repository.transition_task(
            task.id,
            target,
            expected_version=task.state_version,
            event_payload={
                "retry": True,
                "checkpoint_id": checkpoint_id,
                "recovered_from": TaskState.FAILED.value,
            },
            error_message=None,
        )

    async def git_status(self, task_id: str) -> GitStatusOutput:
        invocation = await self._invoke_git(task_id, "git.status", {})
        return GitStatusOutput.model_validate(invocation.output)

    async def git_diff(self, task_id: str) -> GitDiffOutput:
        code_change = self._repository.latest_artifact(
            task_id, ArtifactType.CODE_CHANGE
        )
        changes = CodeChangeArtifact.model_validate(code_change.content)
        invocation = await self._invoke_git(
            task_id,
            "git.diff",
            {"paths": [change.path for change in changes.changes]},
        )
        return GitDiffOutput.model_validate(invocation.output)

    async def git_log(self, task_id: str, limit: int = 10) -> GitLogOutput:
        invocation = await self._invoke_git(
            task_id, "git.log", {"limit": limit}
        )
        return GitLogOutput.model_validate(invocation.output)

    async def commit_task(self, task_id: str, message: str) -> ArtifactRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.COMPLETED:
            raise ValueError("only a completed task can create a Git commit")
        existing = [
            artifact
            for artifact in self._repository.list_artifacts(task_id)
            if artifact.type is ArtifactType.GIT_COMMIT
        ]
        if existing:
            raise ValueError("task already has a Git commit artifact")

        review = ReviewArtifact.model_validate(
            self._repository.latest_artifact(task_id, ArtifactType.REVIEW).content
        )
        report = TestReportArtifact.model_validate(
            self._repository.latest_artifact(
                task_id, ArtifactType.TEST_REPORT
            ).content
        )
        if review.verdict is not ReviewVerdict.APPROVED:
            raise ValueError("latest review is not approved")
        if report.verdict is not TestVerdict.PASSED:
            raise ValueError("latest test report is not passed")

        code_change_record = self._repository.latest_artifact(
            task_id, ArtifactType.CODE_CHANGE
        )
        code_change = CodeChangeArtifact.model_validate(code_change_record.content)
        paths = [change.path for change in code_change.changes]
        expected_hashes = {
            change.path: change.after_sha256 for change in code_change.changes
        }
        project = self._repository.get_project(task.project_id)
        context = ToolContext(
            task_id=task.id,
            agent_name="orchestrator-quality-gate",
            workspace_root=project.root_path,
            permissions=frozenset({"git:commit"}),
        )
        try:
            invocation = await self._tools.invoke(
                "git.commit",
                {
                    "message": message,
                    "paths": paths,
                    "expected_sha256": expected_hashes,
                },
                context,
            )
        except Exception as error:
            raise WorkflowExecutionError("Git commit failed") from error
        output = GitCommitOutput.model_validate(invocation.output)
        commit_artifact = GitCommitArtifact(
            commit_sha=output.commit_sha,
            message=output.message,
            files=output.files,
            code_change_version=code_change_record.version,
        )
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.GIT_COMMIT,
            "orchestrator-quality-gate",
            commit_artifact.model_dump(mode="json"),
        )
        self._checkpoint(task, artifact)
        self._memory_service.consolidate_task(task.id)
        return artifact

    async def _invoke_git(self, task_id: str, tool_name: str, payload: dict):
        task = self._repository.get_task(task_id)
        project = self._repository.get_project(task.project_id)
        context = ToolContext(
            task_id=task.id,
            agent_name="git-inspector",
            workspace_root=project.root_path,
            permissions=frozenset({"git:read"}),
        )
        try:
            return await self._tools.invoke(tool_name, payload, context)
        except Exception as error:
            raise WorkflowExecutionError(f"{tool_name} failed") from error

    async def decide_prd(
        self,
        task_id: str,
        decision: ApprovalDecision,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.PRD_APPROVAL:
            raise ValueError(f"task {task_id} is not waiting for PRD approval")

        if decision is ApprovalDecision.CHANGES_REQUESTED:
            task = self._repository.transition_task(
                task.id,
                TaskState.REQUIREMENT_ANALYZING,
                expected_version=task.state_version,
                event_payload={"decision": decision.value, "feedback": feedback or ""},
            )
            try:
                return await self._run_product(
                    task, feedback=feedback, progress=progress
                )
            except Exception as error:
                self._fail(task.id, error)
                raise WorkflowExecutionError("Product Agent execution failed") from error

        task = self._repository.transition_task(
            task.id,
            TaskState.ARCHITECTING,
            expected_version=task.state_version,
            event_payload={"decision": decision.value},
        )
        try:
            return await self._run_architect(task, progress=progress)
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Architect Agent execution failed") from error

    async def decide_architecture(
        self,
        task_id: str,
        decision: ApprovalDecision,
        feedback: str | None = None,
        selected_option_id: str | None = None,
        autonomous: bool = True,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.ARCH_APPROVAL:
            raise ValueError(f"task {task_id} is not waiting for architecture approval")

        if decision is ApprovalDecision.CHANGES_REQUESTED:
            task = self._repository.transition_task(
                task.id,
                TaskState.ARCHITECTING,
                expected_version=task.state_version,
                event_payload={"decision": decision.value, "feedback": feedback or ""},
            )
            try:
                return await self._run_architect(
                    task, feedback=feedback, progress=progress
                )
            except Exception as error:
                self._fail(task.id, error)
                raise WorkflowExecutionError("Architect Agent execution failed") from error

        architecture_record = self._repository.latest_artifact(
            task.id, ArtifactType.ARCHITECTURE
        )
        architecture = ArchitectureArtifact.model_validate(architecture_record.content)
        if architecture.options:
            if autonomous:
                selected = next(
                    (item for item in architecture.options if item.recommended),
                    architecture.options[0],
                )
            else:
                selected = next(
                    (item for item in architecture.options if item.id == selected_option_id),
                    None,
                )
                if selected is None:
                    raise ValueError("selected architecture option does not exist")
            architecture = architecture.model_copy(
                update={
                    "selected_option_id": selected.id,
                    "selection_mode": "AI_AUTONOMOUS" if autonomous else "USER_SELECTED",
                }
            )
        task = self._repository.transition_task(
            task.id,
            TaskState.CODING,
            expected_version=task.state_version,
            event_payload={
                "decision": decision.value,
                "selected_option_id": architecture.selected_option_id,
                "selection_mode": architecture.selection_mode,
            },
        )
        try:
            return await self._run_developer(task, progress=progress)
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Developer Agent execution failed") from error

    async def run_review(
        self, task_id: str, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.REVIEWING:
            raise ValueError(f"task {task_id} is not ready for code review")
        try:
            return await self._run_reviewer(task, progress=progress)
        except WorkflowExecutionError:
            raise
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Reviewer Agent execution failed") from error

    async def run_tests(
        self, task_id: str, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        task = self._repository.get_task(task_id)
        if task.state is not TaskState.TESTING:
            raise ValueError(f"task {task_id} is not ready for testing")
        try:
            return await self._run_tester(task, progress=progress)
        except WorkflowExecutionError:
            raise
        except Exception as error:
            self._fail(task.id, error)
            raise WorkflowExecutionError("Tester Agent execution failed") from error

    async def _run_product(
        self,
        task: TaskRecord,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        if self._iteration_kind(task.id) is IterationKind.BUG_FIX:
            await self.diagnose_issue(task.id, progress=progress)
        report_progress(progress, 30, "准备需求上下文", "正在加载项目与历史记忆")
        project = self._repository.get_project(task.project_id)
        memory_context = self._memory_context(task, task.requirement)
        prd = await self._product_agent.run(
            requirement=task.requirement,
            project_summary=project.summary,
            feedback=feedback,
            memory_context=memory_context,
            progress=progress,
        )
        report_progress(progress, 90, "保存 PRD", "正在持久化产物与检查点")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.PRD,
            self._product_agent.name,
            prd.model_dump(mode="json"),
        )
        task = self._repository.transition_task(
            task.id,
            TaskState.PRD_APPROVAL,
            expected_version=task.state_version,
            event_payload={"artifact_id": artifact.id},
        )
        self._checkpoint(task, artifact)
        return task

    async def diagnose_issue(
        self,
        task_id: str,
        progress: ProgressReporter | None = None,
    ) -> ArtifactRecord:
        task = self._repository.get_task(task_id)
        if self._iteration_kind(task.id) is not IterationKind.BUG_FIX:
            raise ValueError("only a bug-fix iteration can create a diagnosis")
        existing = [
            artifact
            for artifact in self._repository.list_artifacts(task.id)
            if artifact.type is ArtifactType.DIAGNOSIS
        ]
        if existing:
            return existing[-1]
        project = self._repository.get_project(task.project_id)
        diagnosis = await self._diagnostic_agent.run(
            task_id=task.id,
            workspace_root=project.root_path,
            report=task.requirement,
            project_summary=project.summary,
            progress=progress,
        )
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.DIAGNOSIS,
            self._diagnostic_agent.name,
            diagnosis.model_dump(mode="json"),
        )
        self._memory_service.record_stage_memory(task, artifact)
        return artifact

    async def _run_architect(
        self,
        task: TaskRecord,
        feedback: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        report_progress(progress, 10, "准备架构上下文", "正在加载 PRD 与项目记忆")
        project = self._repository.get_project(task.project_id)
        prd_record = self._repository.latest_artifact(task.id, ArtifactType.PRD)
        prd = PRDArtifact.model_validate(prd_record.content)
        memory_context = self._memory_context(
            task,
            f"{prd.title} {' '.join(item.description for item in prd.requirements)}",
        )
        architecture = await self._architect_agent.run(
            prd=prd,
            project_summary=project.summary,
            feedback=feedback,
            memory_context=memory_context,
            progress=progress,
        )
        report_progress(progress, 90, "保存架构设计", "正在持久化产物与检查点")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.ARCHITECTURE,
            self._architect_agent.name,
            architecture.model_dump(mode="json"),
        )
        task = self._repository.transition_task(
            task.id,
            TaskState.ARCH_APPROVAL,
            expected_version=task.state_version,
            event_payload={"artifact_id": artifact.id},
        )
        self._checkpoint(task, artifact)
        return task

    async def _run_developer(
        self,
        task: TaskRecord,
        feedback: ReviewArtifact | TestReportArtifact | None = None,
        progress: ProgressReporter | None = None,
    ) -> TaskRecord:
        report_progress(progress, 8, "准备开发上下文", "正在加载需求、架构与历史变更")
        project = self._repository.get_project(task.project_id)
        prd_record = self._repository.latest_artifact(task.id, ArtifactType.PRD)
        architecture_record = self._repository.latest_artifact(
            task.id, ArtifactType.ARCHITECTURE
        )
        prd = PRDArtifact.model_validate(prd_record.content)
        architecture = self._architecture_for_task(task.id, architecture_record.content)
        previous_code_changes = [
            artifact
            for artifact in self._repository.list_artifacts(task.id)
            if artifact.type is ArtifactType.CODE_CHANGE
        ]
        implementation_revision = (
            max((artifact.version for artifact in previous_code_changes), default=0) + 1
        )
        code_change = await self._developer_agent.run(
            task_id=task.id,
            workspace_root=project.root_path,
            prd=prd,
            architecture=architecture,
            architecture_version=architecture_record.version,
            implementation_revision=implementation_revision,
            feedback=(
                feedback.model_dump(mode="json") if feedback else None
            ),
            progress=progress,
        )
        report_progress(progress, 92, "保存代码产物", "正在记录文件哈希与检查点")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.CODE_CHANGE,
            self._developer_agent.name,
            code_change.model_dump(mode="json"),
        )
        task = self._repository.transition_task(
            task.id,
            TaskState.REVIEWING,
            expected_version=task.state_version,
            event_payload={"artifact_id": artifact.id},
        )
        self._checkpoint(task, artifact)
        return task

    async def _run_reviewer(
        self, task: TaskRecord, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        report_progress(progress, 8, "准备审查上下文", "正在加载需求、架构与代码变更")
        project = self._repository.get_project(task.project_id)
        prd = PRDArtifact.model_validate(
            self._repository.latest_artifact(task.id, ArtifactType.PRD).content
        )
        architecture = self._architecture_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.ARCHITECTURE
            ).content,
        )
        code_change_record = self._repository.latest_artifact(
            task.id, ArtifactType.CODE_CHANGE
        )
        code_change = CodeChangeArtifact.model_validate(code_change_record.content)
        review = await self._reviewer_agent.run(
            task_id=task.id,
            workspace_root=project.root_path,
            prd=prd,
            architecture=architecture,
            code_change=code_change,
            progress=progress,
        )
        report_progress(progress, 93, "保存审查报告", "正在执行质量门禁判断")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.REVIEW,
            self._reviewer_agent.name,
            review.model_dump(mode="json"),
        )

        if review.verdict is ReviewVerdict.APPROVED:
            task = self._repository.transition_task(
                task.id,
                TaskState.TESTING,
                expected_version=task.state_version,
                event_payload={
                    "artifact_id": artifact.id,
                    "verdict": review.verdict.value,
                },
            )
            self._checkpoint(task, artifact)
            return task

        if code_change_record.version >= MAX_DEVELOPMENT_REVISIONS:
            error = RuntimeError(
                f"review revision limit {MAX_DEVELOPMENT_REVISIONS} exceeded"
            )
            self._fail(task.id, error)
            raise WorkflowExecutionError("Review quality gate revision limit exceeded")

        task = self._repository.transition_task(
            task.id,
            TaskState.CODING,
            expected_version=task.state_version,
            event_payload={
                "artifact_id": artifact.id,
                "verdict": review.verdict.value,
            },
        )
        return await self._run_developer(
            task, feedback=review, progress=progress
        )

    async def _run_tester(
        self, task: TaskRecord, progress: ProgressReporter | None = None
    ) -> TaskRecord:
        report_progress(progress, 8, "准备测试上下文", "正在加载交付产物与审查报告")
        project = self._repository.get_project(task.project_id)
        prd = PRDArtifact.model_validate(
            self._repository.latest_artifact(task.id, ArtifactType.PRD).content
        )
        architecture = self._architecture_for_task(
            task.id,
            self._repository.latest_artifact(
                task.id, ArtifactType.ARCHITECTURE
            ).content,
        )
        code_change = CodeChangeArtifact.model_validate(
            self._repository.latest_artifact(
                task.id, ArtifactType.CODE_CHANGE
            ).content
        )
        review = ReviewArtifact.model_validate(
            self._repository.latest_artifact(task.id, ArtifactType.REVIEW).content
        )
        report = await self._tester_agent.run(
            task_id=task.id,
            workspace_root=project.root_path,
            prd=prd,
            architecture=architecture,
            code_change=code_change,
            review=review,
            progress=progress,
        )
        report_progress(progress, 93, "汇总测试结果", "正在执行最终质量门禁")
        artifact = self._repository.save_artifact(
            task.id,
            ArtifactType.TEST_REPORT,
            self._tester_agent.name,
            report.model_dump(mode="json"),
        )

        if report.verdict is TestVerdict.PASSED:
            task = self._repository.transition_task(
                task.id,
                TaskState.FINAL_VALIDATION,
                expected_version=task.state_version,
                event_payload={
                    "artifact_id": artifact.id,
                    "verdict": report.verdict.value,
                },
            )
            self._checkpoint(task, artifact)
            task = self._repository.transition_task(
                task.id,
                TaskState.COMPLETED,
                expected_version=task.state_version,
                event_payload={"quality_gate": "passed"},
            )
            self._checkpoint(task, artifact)
            self._memory_service.consolidate_task(task.id)
            return task

        if report.verdict is TestVerdict.ENVIRONMENT_ERROR:
            error = RuntimeError("test environment could not execute the test plan")
            self._fail(task.id, error)
            raise WorkflowExecutionError("Test environment error")

        if artifact.version >= MAX_TEST_FAILURES:
            error = RuntimeError(
                f"test failure limit {MAX_TEST_FAILURES} exceeded"
            )
            self._fail(task.id, error)
            raise WorkflowExecutionError("Test quality gate failure limit exceeded")

        task = self._repository.transition_task(
            task.id,
            TaskState.CODING,
            expected_version=task.state_version,
            event_payload={
                "artifact_id": artifact.id,
                "verdict": report.verdict.value,
            },
        )
        return await self._run_developer(
            task, feedback=report, progress=progress
        )

    def _checkpoint(self, task: TaskRecord, artifact: ArtifactRecord) -> None:
        self._repository.save_checkpoint(
            task,
            {
                "task_id": task.id,
                "state": task.state.value,
                "state_version": task.state_version,
                "latest_artifact": {
                    "id": artifact.id,
                    "type": artifact.type.value,
                    "version": artifact.version,
                },
            },
        )
        self._memory_service.record_stage_memory(task, artifact)

    def _memory_context(self, task: TaskRecord, query: str) -> list[dict]:
        results = self._memory_service.search(
            task.project_id,
            MemorySearchQuery(query=query[:500], top_k=6, task_id=task.id),
        )
        return [hit.model_dump(mode="json") for hit in results.hits]

    def _architecture_for_task(
        self, task_id: str, content: dict
    ) -> ArchitectureArtifact:
        architecture = ArchitectureArtifact.model_validate(content)
        if not architecture.options:
            return architecture
        for event in reversed(self._repository.list_events(task_id)):
            selected_option_id = event.payload.get("selected_option_id")
            selection_mode = event.payload.get("selection_mode")
            if selected_option_id and selection_mode:
                return architecture.model_copy(
                    update={
                        "selected_option_id": selected_option_id,
                        "selection_mode": selection_mode,
                    }
                )
        return architecture

    def _iteration_kind(self, task_id: str) -> IterationKind | None:
        for event in self._repository.list_events(task_id):
            value = event.payload.get("iteration_kind")
            if event.event_type == "task.iteration_created" and value:
                return IterationKind(value)
        return None

    def _fail(self, task_id: str, error: Exception) -> None:
        task = self._repository.get_task(task_id)
        message = f"{type(error).__name__}: {error}"[:2000]
        self._repository.transition_task(
            task.id,
            TaskState.FAILED,
            expected_version=task.state_version,
            event_payload={"error_type": type(error).__name__},
            error_message=message,
        )
